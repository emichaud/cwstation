"""MCP models. OAuthAuthorizationCode lives here; APIToken stays in smallstack."""

from django.conf import settings
from django.db import models


class OAuthAuthorizationCode(models.Model):
    """One-shot, PKCE-bound authorization code minted by the consent page.

    Exchanged once for a Bearer token via POST /mcp/oauth/token. After
    redemption, `used_at` is set and `raw_key` is cleared so the row remains
    for audit without leaking material.
    """

    code = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_oauth_codes",
    )
    api_token = models.ForeignKey(
        "smallstack.APIToken",
        on_delete=models.CASCADE,
        related_name="mcp_oauth_codes",
    )
    raw_key = models.CharField(max_length=128, blank=True, default="")
    redirect_uri = models.URLField()
    code_challenge = models.CharField(max_length=128)
    code_challenge_method = models.CharField(max_length=10, default="S256")
    scope = models.CharField(max_length=200, blank=True, default="")
    client_id = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"OAuthCode({self.code[:8]}… → {self.user})"
