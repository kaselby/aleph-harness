### Edit

Performs exact string replacements in files.

- You must Read the file at least once before editing. The tool will fail otherwise.
- When matching text from Read output, preserve the exact indentation as it appears after the line number prefix. Never include the line number prefix itself in the match string.
- The edit will fail if `old_string` is not unique in the file. Provide more surrounding context to make it unique, or use `replace_all` to change every occurrence.
