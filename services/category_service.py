from __future__ import annotations


CATEGORY_NOT_EMPTY_MESSAGE = "文件夹中有文件，请清空后再删除文件夹。"


def validate_category_can_delete(document_count: int) -> None:
    if document_count > 0:
        raise ValueError(CATEGORY_NOT_EMPTY_MESSAGE)
