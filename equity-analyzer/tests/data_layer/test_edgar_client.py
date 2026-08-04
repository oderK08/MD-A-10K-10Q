from equity_analyzer.data_layer.edgar_client import filing_index_url


def test_filing_index_url_strips_leading_zeros_from_cik():
    url = filing_index_url("0000320193", "0000320193-24-000010")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019324000010/0000320193-24-000010-index.htm"
    )


def test_filing_index_url_keeps_dashes_in_the_index_filename_only():
    url = filing_index_url("0000789019", "0001193125-26-323660")
    # accession appears twice: without dashes (the folder), with dashes
    # (the index filename) -- that's the real EDGAR URL convention.
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/789019/"
        "000119312526323660/0001193125-26-323660-index.htm"
    )


def test_filing_index_url_needs_no_network_or_client():
    # pure string construction -- must not raise EdgarClientError or
    # require an EdgarClient instance at all.
    filing_index_url("320193", "0000320193-24-000010")
