import os
import requests
import json
import time
from datetime import datetime
from urllib.parse import urlparse
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Get GitHub token from environment (if available)
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN', '')
HEADERS = {'Authorization': f'token {GITHUB_TOKEN}'} if GITHUB_TOKEN else {}

def parse_github_url(url):
    """Extract owner and repo name from GitHub URL"""
    parsed_url = urlparse(url)
    
    # Check if it's a GitHub URL
    if 'github.com' not in parsed_url.netloc:
        return None, None
    
    # Parse the path to get owner and repo
    path_parts = parsed_url.path.strip('/').split('/')
    if len(path_parts) >= 2:
        owner = path_parts[0]
        repo = path_parts[1]
        return owner, repo
    
    return None, None

def format_date(date_str):
    """Format ISO date to human-readable format"""
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%SZ')
        return date_obj.strftime('%b %d, %Y')
    except:
        return date_str

def handle_api_response(response):
    """Handle API response, including rate limit checks"""
    if response.status_code == 200:
        return response.json(), None
    elif response.status_code == 202:
        # For stats endpoints, GitHub may respond with 202 when computing stats
        # Return empty data instead of showing an error
        return [], None
    elif response.status_code == 403:
        remaining = response.headers.get('X-RateLimit-Remaining', '0')
        reset_time = response.headers.get('X-RateLimit-Reset', '0')
        if remaining == '0':
            reset_datetime = datetime.fromtimestamp(int(reset_time))
            readable_time = reset_datetime.strftime('%H:%M:%S')
            return None, f"API rate limit exceeded. Limit resets at {readable_time}."
        return None, "API access forbidden. Check your token or permissions."
    elif response.status_code == 404:
        return None, "Repository not found. Check the URL and try again."
    else:
        return None, f"API error: {response.status_code}"

def get_commit_activity(owner, repo, max_retries=5):
    """Get commit activity with retry logic for 202 responses"""
    commit_activity_url = f"https://api.github.com/repos/{owner}/{repo}/stats/commit_activity"
    
    for attempt in range(max_retries):
        try:
            response = requests.get(commit_activity_url, headers=HEADERS)
            
            if response.status_code == 200:
                data = response.json()
                if data:  # Check if we got actual data
                    return data, None
                elif attempt < max_retries - 1:
                    # Empty response, might need more time
                    time.sleep(3 * (attempt + 1))
                    continue
                return None, "No commit data available"
            elif response.status_code == 202:
                if attempt < max_retries - 1:
                    # GitHub is computing statistics, wait longer for bigger repos
                    time.sleep(3 * (attempt + 1))
                    continue
                return None, "GitHub is still calculating statistics. Please try again in a few moments."
            elif response.status_code == 401:
                return None, "Authentication failed. Please check your GitHub token."
            elif response.status_code == 403:
                reset_time = datetime.fromtimestamp(int(response.headers.get('X-RateLimit-Reset', time.time())))
                readable_time = reset_time.strftime('%Y-%m-%d %H:%M:%S')
                return None, f"API rate limit exceeded. Limit resets at {readable_time}."
            elif response.status_code == 404:
                return None, "Repository not found or stats are not available. For private repositories, make sure your token has the required permissions."
            else:
                return None, f"GitHub API error: {response.status_code}"
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                return None, f"Network error: {str(e)}"
            time.sleep(2)
            continue
    
    return None, "Could not retrieve commit activity after maximum retries"

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    repo_url = request.form.get('repo_url', '')
    owner, repo = parse_github_url(repo_url)
    
    if not owner or not repo:
        return render_template('index.html', 
                            error="Invalid GitHub URL. Please enter a valid repository URL.")
    
    # Fetch repo metadata
    repo_url = f"https://api.github.com/repos/{owner}/{repo}"
    response = requests.get(repo_url, headers=HEADERS)
    repo_data, error = handle_api_response(response)
    
    if error:
        return render_template('index.html', error=error)
    
    # Format dates
    if repo_data.get('created_at'):
        repo_data['created_at'] = format_date(repo_data['created_at'])
    if repo_data.get('updated_at'):
        repo_data['updated_at'] = format_date(repo_data['updated_at'])
    if repo_data.get('pushed_at'):
        repo_data['pushed_at'] = format_date(repo_data['pushed_at'])
    
    # Fetch contributors
    contributors_url = f"https://api.github.com/repos/{owner}/{repo}/contributors"
    response = requests.get(contributors_url, headers=HEADERS, params={'per_page': 10})
    contributors_data, error = handle_api_response(response)
    
    if error:
        contributors_data = []
        contributor_error = error
    else:
        contributor_error = None
    
    # Fetch commit activity with retry logic
    commit_activity, error = get_commit_activity(owner, repo)
    
    if error:
        commit_activity = []
        commit_error = error
    else:
        commit_error = None
    
    # Calculate some additional metrics
    if commit_activity:
        total_commits = sum(week['total'] for week in commit_activity)
        # Only consider weeks with actual commits for the average
        active_weeks = [week['total'] for week in commit_activity if week['total'] > 0]
        weekly_average = round(sum(active_weeks) / len(active_weeks), 2) if active_weeks else 0
        
        commit_data = {
            'total_commits': total_commits,
            'weekly_average': weekly_average,
            'recent_weeks': commit_activity[-4:] if len(commit_activity) >= 4 else commit_activity
        }
    else:
        commit_data = {
            'total_commits': 0,
            'weekly_average': 0,
            'recent_weeks': []
        }
    
    # Get remaining rate limit info
    rate_limit_url = "https://api.github.com/rate_limit"
    response = requests.get(rate_limit_url, headers=HEADERS)
    if response.status_code == 200:
        rate_limit_data = response.json()['resources']['core']
        rate_limit = {
            'remaining': rate_limit_data['remaining'],
            'limit': rate_limit_data['limit'],
            'reset_time': datetime.fromtimestamp(rate_limit_data['reset']).strftime('%H:%M:%S')
        }
    else:
        rate_limit = {'remaining': 'Unknown', 'limit': 'Unknown', 'reset_time': 'Unknown'}
    
    return render_template('index.html', 
                         repo_data=repo_data,
                         contributors=contributors_data,
                         commit_data=commit_data,
                         contributor_error=contributor_error,
                         commit_error=commit_error,
                         rate_limit=rate_limit,
                         repo_url=repo_url)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)