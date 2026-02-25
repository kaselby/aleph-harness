### WebFetch

Fetches a URL and answers a question about its content. Takes a URL and a prompt as parameters.

The tool does not return raw page content. Internally, it converts HTML to markdown, then passes the content through a small summarizer model (Haiku) with your prompt to extract relevant information. Only the summarizer's response reaches you. This means:
- Your prompt matters — vague prompts produce vague results. Ask for specific information.
- Detail can be lost in summarization. If you need exact text (code snippets, configuration values), say so explicitly in the prompt.
- ~80 trusted documentation domains (MDN, Python docs, React, etc.) get more generous extraction. Non-trusted sites are limited to shorter quotes.

Other details:
- Results are cached for 15 minutes.
- Content is truncated to 100KB before summarization.
- Cross-host redirects are not followed automatically — the tool will tell you the redirect URL and you must fetch it separately.
