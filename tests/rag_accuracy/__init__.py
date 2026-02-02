"""
RAG Accuracy Testing Module

This module contains test dataset generation and RAGAS evaluation
for validating RAG retrieval accuracy after full index ingestion.

Usage:
    # Generate test dataset from PDFs
    python -m tests.rag_accuracy.generate_dataset --count 100

    # Run RAGAS evaluation
    python -m tests.rag_accuracy.run_evaluation

    # Or use the scripts directly
    python scripts/generate_test_dataset_from_pdfs.py
    python scripts/run_ragas_evaluation.py
"""

from pathlib import Path

# Default paths
TEST_DATA_DIR = Path(__file__).parent / "data"
DEFAULT_DATASET_PATH = TEST_DATA_DIR / "test_dataset_v3.json"
DEFAULT_RAGAS_OUTPUT = TEST_DATA_DIR / "ragas_evaluation_dataset.json"
