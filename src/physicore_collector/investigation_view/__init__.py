"""M3e: investigation view over the M3d localization report (Markdown and a single offline HTML page).

A pure presentation layer: it reads one M3d report (schema 1.0), adds no analysis and no finding, and every sentence it generates comes from a fixed
template. Data text (topics, names, identities) is cleaned and escaped for the target format. Nothing is written anywhere by this package."""
VIEW_VERSION = "1.0.0"
SUPPORTED_SCHEMA = "1.0"
MAX_REPORT_BYTES = 64 * 1024 * 1024
