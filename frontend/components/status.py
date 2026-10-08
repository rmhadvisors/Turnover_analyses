"""Colours, emoji and labels for alert statuses (shared by table, badges and pages)."""

BAND_STYLE = {
    "significant_increase": ("🔴", "#b42318"),
    "moderate_increase": ("🟡", "#a15c07"),
    "normal": ("🟢", "#137333"),
    "moderate_decrease": ("🟡", "#a15c07"),
    "significant_decrease": ("🔴", "#b42318"),
}
LIMIT_STYLE = {
    "crossed": ("🔴", "#b42318"),
    "approaching": ("🟡", "#a15c07"),
    "below": ("🟢", "#137333"),
}
SEVERITY_STYLE = {
    "critical": ("🔴", "#b42318"),
    "high": ("🟠", "#c4320a"),
    "warning": ("🟡", "#a15c07"),
    "info": ("🟢", "#137333"),
}
NEUTRAL = ("⚪", "#5f6368")

STATUS_TEXT = {
    "significant_increase": "Significant Increase",
    "moderate_increase": "Moderate Increase",
    "normal": "Normal",
    "moderate_decrease": "Moderate Decrease",
    "significant_decrease": "Significant Decrease",
    "crossed": "Crossed",
    "approaching": "Approaching",
    "below": "Below limit",
}
SIGN_FLAGS = {"turned_to_loss": "Turned to Loss", "turned_to_profit": "Turned to Profit"}


def style_for(status: str | None) -> tuple[str, str]:
    """(emoji, colour) for a band or limit status; grey when unknown / not comparable."""
    return BAND_STYLE.get(status or "") or LIMIT_STYLE.get(status or "") or NEUTRAL


def status_text(status: str | None) -> str:
    return STATUS_TEXT.get(status or "", status or "—")


def with_emoji(status: str | None, label: str | None = None) -> str:
    emoji, _ = style_for(status)
    return f"{emoji} {label or status_text(status)}"
