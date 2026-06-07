"""Verification script for retrieval scoring and labels."""

import os
import sys

# Ensure backend root is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from retrieval.searcher import search
from retrieval.query_intent import classify_query_intent
import retrieval.config as r_config

# Configure environment/config for CodeSeek local repository
os.environ["QDRANT_COLLECTION_NAME"] = "repository_chunks__local__atharvapagar04_codeseek"
os.environ["RETRIEVAL_REPO_ROOT"] = "/home/arch/DEV/CodeSeek"
os.environ["CODESEEK_TENANT_ID"] = "local"
os.environ["RETRIEVAL_ENABLE_DENSE"] = "1"
os.environ["RETRIEVAL_ENABLE_LEXICAL"] = "0"  # focus on dense + labels

# Force reload config values that depend on env vars
r_config.REPO_ROOT = "/home/arch/DEV/CodeSeek"
r_config.COLLECTION_NAME = "repository_chunks__local__atharvapagar04_codeseek"

def test_query_1():
    print("\n--- Testing Query 1: 'How does auth work?' ---")
    query_info = {
        "raw_query": "How does auth work?",
        "intent": "SEMANTIC",
        "primary_intent": "SEMANTIC",
        "entities": {"symbols": [], "files": []}
    }
    
    # Classify to simulate the complete query processor flow
    intent_class = classify_query_intent("How does auth work?")
    query_info["intent"] = intent_class["intent"]
    query_info["primary_intent"] = intent_class["intent"]
    
    results = search(query_info)
    
    print(f"Top 5 paths found for query '{query_info['raw_query']}':")
    top_paths = []
    for i, res in enumerate(results[:5], start=1):
        path = res.get("relative_path")
        score = res.get("retrieval_score")
        final_score = res.get("final_score")
        labels = res.get("labels", [])
        print(f"  {i}. {path} (retrieval_score={score}, final_score={final_score})")
        print(f"     labels: {labels}")
        if path:
            top_paths.append(path)
        
    assert any("auth" in p.lower() for p in top_paths), "Expected auth-related files in top 5"
    print("Query 1: SUCCESS")


def test_query_2():
    print("\n--- Testing Query 2: 'How is session token validation implemented?' ---")
    query_info = {
        "raw_query": "How is session token validation implemented?",
        "intent": "SEMANTIC",
        "primary_intent": "SEMANTIC",
        "entities": {"symbols": [], "files": []}
    }
    
    intent_class = classify_query_intent("How is session token validation implemented?")
    query_info["intent"] = intent_class["intent"]
    query_info["primary_intent"] = intent_class["intent"]
    
    results = search(query_info)
    
    print(f"Top 3 paths found for query '{query_info['raw_query']}':")
    for i, res in enumerate(results[:3], start=1):
        path = res.get("relative_path")
        symbol = res.get("symbol_name")
        score = res.get("retrieval_score")
        final_score = res.get("final_score")
        labels = res.get("labels", [])
        print(f"  {i}. {path}::{symbol} (retrieval_score={score}, final_score={final_score})")
        print(f"     labels: {labels}")
        
    top_result = results[0]
    top_symbol = top_result.get("symbol_name", "")
    top_path = top_result.get("relative_path", "")
    print(f"Top result is: {top_path}::{top_symbol}")
    
    assert any(
        res.get("relative_path") and (
            "session" in res.get("relative_path", "").lower() or 
            "auth" in res.get("relative_path", "").lower()
        )
        for res in results[:3]
    ), "Expected session or auth files in top 3"
    print("Query 2: SUCCESS")


def test_query_3():
    print("\n--- Testing Query 3: 'Show me the session validation code' ---")
    query_info = {
        "raw_query": "Show me the session validation code",
        "intent": "SEMANTIC",
        "primary_intent": "SEMANTIC",
        "entities": {"symbols": [], "files": []}
    }
    
    intent_class = classify_query_intent("Show me the session validation code")
    query_info["intent"] = intent_class["intent"]
    query_info["primary_intent"] = intent_class["intent"]
    
    results = search(query_info)
    
    print(f"Top 3 labels found for query '{query_info['raw_query']}':")
    top_labels_list = []
    for i, res in enumerate(results[:3], start=1):
        path = res.get("relative_path")
        labels = res.get("labels", [])
        print(f"  {i}. {path}")
        print(f"     labels: {labels}")
        top_labels_list.append(labels)
        
    print(f"Query classified primary intent: {intent_class['intent']}")
    print(f"Boost labels: {intent_class['boost_labels']}")
    
    assert any("question_use:code-snippet" in labels for labels in top_labels_list), \
        "Expected 'question_use:code-snippet' to be boosted and present in top results"
    print("Query 3: SUCCESS")


def test_query_4():
    print("\n--- Testing Query 4: 'How do I change the provider validation logic?' ---")
    query_info = {
        "raw_query": "How do I change the provider validation logic?",
        "intent": "SEMANTIC",
        "primary_intent": "SEMANTIC",
        "entities": {"symbols": [], "files": []}
    }
    
    intent_class = classify_query_intent("How do I change the provider validation logic?")
    query_info["intent"] = intent_class["intent"]
    query_info["primary_intent"] = intent_class["intent"]
    
    results = search(query_info)
    
    print(f"Top 3 labels found for query '{query_info['raw_query']}':")
    top_labels_list = []
    for i, res in enumerate(results[:3], start=1):
        path = res.get("relative_path")
        labels = res.get("labels", [])
        print(f"  {i}. {path}")
        print(f"     labels: {labels}")
        top_labels_list.append(labels)
        
    print(f"Query classified primary intent: {intent_class['intent']}")
    print(f"Boost labels: {intent_class['boost_labels']}")
    
    assert any("question_use:implementation" in labels for labels in top_labels_list), \
        "Expected 'question_use:implementation' to be boosted and present in top results"
    print("Query 4: SUCCESS")


if __name__ == "__main__":
    try:
        test_query_1()
        test_query_2()
        test_query_3()
        test_query_4()
        print("\nALL RETRIEVAL TESTS PASSED SUCCESSFULY!")
    except AssertionError as exc:
        print(f"\nAssertion Error: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"\nUnexpected Error: {exc}")
        sys.exit(1)
