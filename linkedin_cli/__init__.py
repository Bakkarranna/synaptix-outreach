"""linkedin_cli — standalone, Django-free LinkedIn interaction library.

Owns the LinkedIn *platform* mechanics — navigation, login, the Voyager API
client, profile/conversation scraping, and the connect/message/status/thread
verbs. It holds no database and no campaign/CRM context: every verb runs
against a browser session supplied by the caller (see ``session.LinkedInSession``).

While the boundary is being hardened the package lives inside the OpenOutreach
repo; the old ``linkedin/`` import paths re-export from here so the application
keeps working unchanged.
"""

__version__ = "0.0.0"
