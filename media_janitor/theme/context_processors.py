from django.http import HttpRequest


def theme_names(request: HttpRequest) -> dict[str, str]:
    """
    Add template variables for the theme names, so we only have to
    hard-code theme here and in the CSS config
    """
    return {"light_theme": "corporate", "dark_theme": "business"}
