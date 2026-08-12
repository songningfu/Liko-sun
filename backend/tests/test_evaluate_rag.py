from scripts.evaluate_rag import campus_from_requirement


def test_campus_from_requirement() -> None:
    assert campus_from_requirement("粤海校区") == "粤海"
    assert campus_from_requirement("丽湖校区") == "丽湖"
    assert campus_from_requirement("粤海/丽湖分校区") == "通用"
    assert campus_from_requirement("通用") == "通用"
