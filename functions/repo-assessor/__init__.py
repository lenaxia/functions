"""repo-assessor — Reddit r/selfhosted project announcement assessor.

Triggered by a Fission TimeTrigger. Polls r/selfhosted for new posts
containing GitHub links, classifies whether the post is announcing a new
project or major update, and if so spins up an ephemeral LLMSafeSpaces
workspace to run a structured assessment, then replies under the
canonical "how was AI used" sticky comment with a summary.
"""
