import pytest
from system.browser.profile import BrowserProfile

def test_browser_profile_default_args():
    # We must provide user_data_dir as get_args() enforces it
    profile = BrowserProfile(headless=True, disable_security=True, user_data_dir='/tmp/mock_user_data_dir')
    args = profile.get_args()
    
    # Check core anti-bot args or security override args
    # (Just verifying the function successfully generates a list of arguments)
    assert any('--no-sandbox' in arg for arg in args) or any('--disable-web-security' in arg for arg in args)
    
def test_browser_profile_chrome_instance_path():
    profile = BrowserProfile(executable_path="/usr/bin/google-chrome")
    assert str(profile.executable_path) == "/usr/bin/google-chrome"
