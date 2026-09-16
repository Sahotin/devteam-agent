from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import replace
from pathlib import Path

from backend.app.container import ApplicationContainer
from backend.app.core.config import Settings
from backend.app.domain.models import ProjectCreate
from backend.app.rag.evaluation import RetrievalCase, evaluate_retrieval


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare BM25, vector and hybrid retrieval")
    parser.add_argument("workspace", type=Path, help="repository root to index")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("docs/rag/retrieval_eval_cases.json"),
        help="JSON file containing verified retrieval cases",
    )
    parser.add_argument("--k", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_cases = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = [RetrievalCase.model_validate(item) for item in raw_cases]
    with tempfile.TemporaryDirectory(prefix="devteam-retrieval-eval-") as temp_dir:
        settings = replace(
            Settings.from_env(),
            database_url=f"sqlite:///{Path(temp_dir) / 'evaluation.db'}",
            database_auto_create=True,
        )
        container = ApplicationContainer.build(settings)
        project = container.repository.create_project(
            ProjectCreate(
                name="Retrieval evaluation",
                root_path=str(args.workspace.resolve()),
                summary="isolated retrieval evaluation",
            )
        )
        try:
            report = container.index_service.index_project(project.id)
            chunks = container.repository.code_chunks(project.id)
            print(
                f"Indexed {report.scanned_files} files / {report.created_chunks} chunks "
                f"(version {report.index_version})"
            )
            print("Mode      Recall@K  MRR       Precision@K")
            for mode in ("bm25", "vector", "hybrid"):
                metrics = evaluate_retrieval(
                    container.index_service.retriever,
                    chunks,
                    cases,
                    k=args.k,
                    mode=mode,
                )
                print(
                    f"{mode:<10}{metrics.recall_at_k:<10.3f}"
                    f"{metrics.mean_reciprocal_rank:<10.3f}{metrics.precision_at_k:.3f}"
                )
        finally:
            container.engine.dispose()


if __name__ == "__main__":
    main()
