import os
import re

TARGET_DIR = r"c:\Users\maham\fyp_2\browser-use-main\browser-use-main\MIRA"
# Match 'browser_use' exactly as a word boundary
PATTERN = re.compile(r'\bbrowser_use\b')

def refactor_directory(directory):
    for root, dirs, files in os.walk(directory):
        # Skip standard ignore directories
        if '.git' in root or '.venv' in root or '.pytest_cache' in root:
            continue
            
        for file in files:
            if not file.endswith('.py') and file != 'pyproject.toml' and not file.endswith('.md'):
                continue
                
            # Let's not modify this script itself just in case
            if file == 'refactor_system.py':
                continue
                
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                if PATTERN.search(content):
                    new_content = PATTERN.sub('system', content)
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    print(f"Updated: {filepath}")
            except Exception as e:
                print(f"Error reading {filepath}: {e}")

if __name__ == '__main__':
    refactor_directory(TARGET_DIR)
    print("Refactoring string replacements complete.")
