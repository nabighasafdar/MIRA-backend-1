import os
from reportlab.platypus import SimpleDocTemplate, Paragraph, Preformatted, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import letter

# Root directory (change if needed)
ROOT_DIR = "."

# Output PDF
pdf_file = "full_repository.pdf"

# File types to include
extensions = [".py", ".js", ".cpp", ".html", ".css", ".java"]

# Folders to ignore
exclude_dirs = [".git", "__pycache__", "node_modules", "venv", ".venv", ".pytest_cache", ".idea", ".vscode"]

# Create document
doc = SimpleDocTemplate(pdf_file, pagesize=letter)
styles = getSampleStyleSheet()

content = []

# Get top-level directories (sub-repos)
subrepos = [d for d in os.listdir(ROOT_DIR) if os.path.isdir(os.path.join(ROOT_DIR, d)) and d not in exclude_dirs]

for repo in subrepos:
    repo_path = os.path.join(ROOT_DIR, repo)

    # 🔥 Repo Title
    content.append(Paragraph(f"<b>Directory: {repo}</b>", styles["Heading1"]))
    content.append(Spacer(1, 20))

    for root, dirs, files in os.walk(repo_path):
        # Remove excluded directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs]

        for file in files:
            if any(file.endswith(ext) for ext in extensions):
                filepath = os.path.join(root, file)

                # File heading
                content.append(Paragraph(f"<b>{filepath}</b>", styles["Heading3"]))
                content.append(Spacer(1, 10))

                try:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        code = f.read()

                    # Code block
                    content.append(Preformatted(code, styles["Code"]))
                    content.append(Spacer(1, 20))

                except Exception as e:
                    content.append(Paragraph(f"Error reading {filepath}: {e}", styles["Normal"]))

    # Page break after each repo
    content.append(PageBreak())

# Build PDF
doc.build(content)

print("PDF created successfully:", pdf_file)
