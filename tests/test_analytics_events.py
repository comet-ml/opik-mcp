from opik_mcp.analytics.events import bucket_count, bucket_text_len, bucket_tokens


def test_bucket_tokens_thresholds() -> None:
    assert bucket_tokens(0) == "<2k"
    assert bucket_tokens(1999) == "<2k"
    assert bucket_tokens(2000) == "2k-8k"
    assert bucket_tokens(7999) == "2k-8k"
    assert bucket_tokens(8000) == "8k-32k"
    assert bucket_tokens(31_999) == "8k-32k"
    assert bucket_tokens(32_000) == ">32k"
    assert bucket_tokens(10_000_000) == ">32k"


def test_bucket_text_len_thresholds() -> None:
    assert bucket_text_len("") == "<100"
    assert bucket_text_len("a" * 99) == "<100"
    assert bucket_text_len("a" * 100) == "100-1000"
    assert bucket_text_len("a" * 999) == "100-1000"
    assert bucket_text_len("a" * 1000) == ">1000"


def test_bucket_count_thresholds() -> None:
    assert bucket_count(0) == "0"
    assert bucket_count(1) == "1-10"
    assert bucket_count(10) == "1-10"
    assert bucket_count(11) == "11-100"
    assert bucket_count(100) == "11-100"
    assert bucket_count(101) == "101-1000"
    assert bucket_count(10_000) == ">1000"
