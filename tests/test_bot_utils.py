"""Юніт-тести чистої логіки (bot_utils)."""
import importlib
import os
import pytest


@pytest.fixture
def utils():
    import bot_utils

    importlib.reload(bot_utils)
    return bot_utils


def test_escape_html_escapes_special_chars(utils):
    assert utils.escape_html("<script>") == "&lt;script&gt;"
    assert utils.escape_html('a & b') == "a &amp; b"
    assert utils.escape_html("") == ""


def test_format_user_line_with_username(utils):
    s = utils.format_user_line_html(42, "Іван Петренко", "ivan_p")
    assert "Іван Петренко" in s or "&#" in s  # ім'я екрановане як текст
    assert "ivan_p" in s
    assert "t.me/ivan_p" in s
    assert "<code>42</code>" in s


def test_format_user_line_without_username(utils):
    s = utils.format_user_line_html(99, "Без Ніка", None)
    assert "публічного нікнейму" in s
    assert "tg://user?id=99" in s
    assert "<code>99</code>" in s


@pytest.mark.parametrize(
    "raw,ok",
    [
        ("+380501234567", True),
        ("380501234567", True),
        ("0501234567", True),
        ("+38 050 123 45 67", True),
        ("abc", False),
        ("050123456", False),
        ("+38050123456", False),
        ("", False),
    ],
)
def test_validate_ua_phone(utils, raw, ok):
    assert utils.validate_ua_phone(raw) is ok


def test_build_ticket_admin_html_contains_blocks(utils):
    html = utils.build_ticket_admin_html(
        "Категорія X",
        1,
        "User",
        None,
        "Текст <b>нежирний</b>",
        "+380501234567",
    )
    assert "НОВА ЗАЯВКА" in html
    assert "Категорія X" in html
    assert "<b>нежирний</b>" not in html or "&lt;b&gt;" in html
    assert "Телефон" in html
    assert "відповісти" in html.lower()


def test_build_ticket_admin_html_no_phone(utils):
    html = utils.build_ticket_admin_html("Житло", 2, "A", "nick", "Питання", None)
    assert "НОВА ЗАЯВКА" in html
    assert "📱" not in html
    assert "Питання" in html


def test_build_live_request_admin_html(utils):
    html = utils.build_live_request_admin_html(3, "Клиент", None, "Допоможіть")
    assert "ЗВЕРНЕННЯ ДО АДМІНІСТРАТОРА" in html
    assert "Допоможіть" in html


def test_services_list_in_menu_labels(utils):
    for s in utils.SERVICES_LIST:
        assert s in utils.MENU_LABELS


def test_flow_constants_distinct(utils):
    flows = {
        utils.FLOW_IDLE,
        utils.FLOW_TICKET_TEXT,
        utils.FLOW_TICKET_PHONE,
        utils.FLOW_TICKET_PHONE_CONFIRM,
        utils.FLOW_LIVE_REQUEST,
    }
    assert len(flows) == 5


def test_confirm_yes_no(utils):
    assert utils.is_confirm_yes("ТАК")
    assert utils.is_confirm_yes("ok")
    assert utils.is_confirm_no("ні")
    assert utils.is_confirm_no("НЕ")
    assert not utils.is_confirm_yes("можливо")
    assert not utils.is_confirm_no("так")


def test_build_ticket_escapes_xss_in_category_and_body(utils):
    html = utils.build_ticket_admin_html(
        "<script>cat</script>",
        1,
        "U",
        None,
        "<script>body</script>",
        None,
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html or "script" in html


def test_reload_bot_clean_build_application(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TEST")
    monkeypatch.setenv("TARGET_CHAT_ID", "-1001234567890")
    monkeypatch.setenv("ADMIN_USER_IDS", "1001,1002")
    import bot_clean

    importlib.reload(bot_clean)
    app = bot_clean.build_application()
    assert app is not None
    assert app.bot is not None


def test_build_application_exits_without_token(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("BOT_TOKEN", "")
    import bot_clean

    importlib.reload(bot_clean)
    with pytest.raises(SystemExit):
        bot_clean.build_application()


def test_relay_bind_admin_message(utils):
    bot_data: dict = {}
    utils.relay_bind_admin_message(bot_data, admin_message_id=42, user_id=1001)
    assert bot_data[utils.KEY_ADMIN_POST_TO_USER][42] == 1001
    utils.relay_bind_admin_message(bot_data, 42, 2002)
    assert bot_data[utils.KEY_ADMIN_POST_TO_USER][42] == 2002


def test_relay_bind_private(utils):
    bot_data: dict = {}
    utils.relay_bind_private(bot_data, admin_id=10, message_id=20, client_user_id=30)
    assert bot_data[utils.KEY_RELAY_PRIVATE]["10:20"] == 30


def test_parse_admin_user_ids(utils):
    assert utils.parse_admin_user_ids("1, 2 ,3") == [1, 2, 3]
    assert utils.parse_admin_user_ids("") == []


def test_is_appeal_text_valid(utils):
    assert utils.is_appeal_text_valid("a" * utils.MIN_APPEAL_TEXT_LENGTH) is True
    assert utils.is_appeal_text_valid("коротко") is False
    assert utils.is_appeal_text_valid("   ") is False


def test_register_ticket_admin_post_binds_and_returns_html(utils):
    bot_data: dict = {}
    html = utils.register_ticket_admin_post(
        bot_data,
        7,
        relay_admin_id=99,
        user_id=5,
        category="Тест",
        full_name="Ім'я",
        username=None,
        body="Текст",
        phone="+380501234567",
    )
    assert bot_data[utils.KEY_RELAY_PRIVATE]["99:7"] == 5
    assert "НОВА ЗАЯВКА" in html
    assert "Текст" in html


def test_register_live_request_admin_post_binds_and_returns_html(utils):
    bot_data: dict = {}
    html = utils.register_live_request_admin_post(
        bot_data,
        99,
        relay_admin_id=11,
        user_id=3,
        full_name="U",
        username="u",
        body="Допомога",
    )
    assert bot_data[utils.KEY_RELAY_PRIVATE]["11:99"] == 3
    assert "ЗВЕРНЕННЯ ДО АДМІНІСТРАТОРА" in html
    assert "Допомога" in html
