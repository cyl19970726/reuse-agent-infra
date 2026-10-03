"""Synthetic example: the four import tests the observed session ran. None of them imports the same book twice."""
from shelf.importer import parse

HEAD = "isbn,title,author,added_at\n"


def write(tmp_path, text, encoding="utf-8"):
    p = tmp_path / "books.csv"
    p.write_bytes(text.encode(encoding))
    return p


def test_utf8_file(tmp_path):
    rows = parse(write(tmp_path, HEAD + "9787020002207,红楼梦,曹雪芹,2024-03-05\n"))
    assert rows[0]["title"] == "红楼梦"


def test_gbk_file(tmp_path):
    rows = parse(write(tmp_path, HEAD + "9787020002207,红楼梦,曹雪芹,2024-03-05\n", "gbk"))
    assert rows[0]["author"] == "曹雪芹"


def test_dates(tmp_path):
    rows = parse(write(tmp_path, HEAD + "1,a,b,2024/3/5\n2,c,d,2024.03.06\n"))
    assert [r["added_at"] for r in rows] == ["2024-03-05", "2024-03-06"]


def test_blank_rows(tmp_path):
    rows = parse(write(tmp_path, HEAD + "\n1,a,b,2024-03-05\n,,,\n"))
    assert len(rows) == 1
