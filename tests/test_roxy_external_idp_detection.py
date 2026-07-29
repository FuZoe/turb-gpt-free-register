from core.roxy_registration import _is_external_idp_url


def test_email_query_is_not_treated_as_external_idp():
    url = (
        "https://chatgpt.com/auth/login?email="
        "user%40y66fvr.84gkyc.7xkp6u.flqssoft.xyz"
    )

    assert _is_external_idp_url(url) is False


def test_known_external_idp_hosts_and_paths_are_detected():
    assert _is_external_idp_url("https://accounts.google.com/o/oauth2/auth?client_id=x") is True
    assert _is_external_idp_url("https://github.com/login/oauth/authorize?client_id=x") is True
    assert _is_external_idp_url("https://idp.example.com/sso/login?email=user@example.com") is True
