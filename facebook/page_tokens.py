"""Takes a System User Token and prints out a list of pages and their Page Access Tokens."""
import requests


def get_fb_token(app_id, app_secret):
    """Obtains the Access Token with the provided app ID and app secret"""
    url = "https://graph.facebook.com/v14.0/oauth/access_token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": app_id,
        "client_secret": app_secret,
    }
    response = requests.post(url, params=payload, timeout=30)
    print(response)
    return response.json()["access_token"]


def get_pages(user_token):
    """Returns a list of pages based on the system user token"""
    url = f"https://graph.facebook.com/v19.0/me/accounts?access_token={user_token}"
    print(url)
    response = requests.get(url, timeout=30)
    return response.json()


print(get_pages("<YOUR_SYSTEM_USER_TOKEN_HERE>"))
