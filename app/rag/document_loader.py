#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/1/9 15:05
@Author  : GGBOND
@File    : document_loader.py
@Software: PyCharm
"""
"""
文档加载与预处理
"""
import os
from pathlib import Path
from typing import List
from langchain_core.documents import Document
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from app.utils.logger import app_logger


class DocumentManager:
    """文档管理器"""

    def __init__(self, base_dir: str = None):
        if base_dir is None:
            project_root = Path(__file__).parent.parent.parent
            self.base_dir = project_root / "data" / "documents"
        else:
            self.base_dir = Path(base_dir)

    def load_destination_documents(self) -> List[Document]:
        """加载所有目的地文档"""

        destinations_dir = self.base_dir / "destinations"

        if not destinations_dir.exists():
            app_logger.warning(f"目的地文档目录不存在: {destinations_dir}")
            return []

        loader = DirectoryLoader(
            str(destinations_dir),
            glob="**/*.md",
            loader_cls=TextLoader,
            loader_kwargs={"encoding": "utf-8"}
        )

        documents = loader.load()
        app_logger.info(f"加载了 {len(documents)} 个目的地文档")

        for doc in documents:
            doc.metadata["source_type"] = "destination_guide"
            doc.metadata["category"] = "destinations"

        return documents

    def load_food_documents(self) -> List[Document]:
        """加载美食文档"""
        pass

    def load_accommodation_documents(self) -> List[Document]:
        """加载住宿文档"""
        pass

