import os
import json
import yaml
from pathlib import Path

os.environ["CODESEEK_DB_BACKEND"] = "sqlite"
os.environ["CODESEEK_DB_PATH"] = "/tmp/codeseek.sqlite3"

from retrieval.db import db_cursor
session_id = "d0080c2f183740699918777d953f25fa"
with db_cursor() as (conn, cursor):
    row = cursor.execute("SELECT collection, repo_root FROM repo_sessions WHERE id = ?", (session_id,)).fetchone()
    os.environ["QDRANT_COLLECTION_NAME"] = row["collection"]
    os.environ["RETRIEVAL_REPO_ROOT"] = row["repo_root"]

from retrieval.main import _resolve_query_info
from retrieval.searcher import (
    _dense_search,
    _lexical_search,
    _metadata_search,
    _exact_entity_search,
    _dependency_search,
    _merge_results,
    _rerank_with_query_tokens,
    search
)
from retrieval.query_intent import classify_query_intent, map_label_intent_to_reranker_intent
from retrieval.memory import ConversationMemory

def run_analysis():
    with open("../evals/golden_queries.yaml") as f:
        golden = yaml.safe_load(f)

    target_ids = {"q001", "q002", "q003", "q004", "q007", "q009"}
    target_queries = [g for g in golden if g["id"] in target_ids]

    memory = ConversationMemory(max_turns=10)

    results = []

    for gq in target_queries:
        query_id = gq["id"]
        raw_query = gq["query"]
        expected_files = gq.get("expected_files", [])
        expected_symbols = gq.get("expected_symbols", [])

        # Get query info
        q_info = _resolve_query_info(raw_query, memory)
        entities = q_info.get("entities", {})

        # Intent
        label_profile = classify_query_intent(raw_query)
        label_intent = label_profile.get("intent", "general_context")
        
        is_followup = False
        is_low_context = False
        reranker_intent = map_label_intent_to_reranker_intent(
            label_intent,
            query=raw_query,
            is_followup=is_followup,
            is_low_context=is_low_context,
            extracted_entities=entities
        )

        boost_labels = label_profile.get("labels", [])

        # Run layers
        dense = _dense_search(raw_query)
        lexical = _lexical_search(raw_query)
        metadata = _metadata_search(raw_query, entities)
        exact = _exact_entity_search(entities)
        dependency = _dependency_search(entities) if label_intent == "DEPENDENCY" or q_info.get("intent") == "DEPENDENCY" else []

        # Merge
        merged = _merge_results(dense, lexical, metadata, exact, dependency)
        
        # Final reranked
        final_list = search(q_info)

        def format_layer_top10(layer_results, is_tuple_list=True):
            formatted = []
            for item in layer_results[:10]:
                if is_tuple_list and isinstance(item, tuple):
                    payload, score, source = item
                else:
                    payload = item
                    score = item.get("retrieval_score") or item.get("score")
                    source = item.get("source_layer") or "merged"
                
                formatted.append({
                    "relative_path": payload.get("relative_path"),
                    "symbol_name": payload.get("symbol_name"),
                    "chunk_type": payload.get("chunk_type"),
                    "score": score,
                    "source": source
                })
            return formatted

        # Check appearance
        all_layers = {
            "dense": dense,
            "lexical": lexical,
            "metadata": metadata,
            "exact": exact,
            "dependency": dependency,
            "merged": merged,
            "final": final_list
        }

        layer_appearance = {}
        for name, layer_res in all_layers.items():
            appeared = False
            for item in layer_res:
                if isinstance(item, tuple):
                    payload = item[0]
                else:
                    payload = item
                
                rel_path = payload.get("relative_path", "") or ""
                sym_name = payload.get("symbol_name", "") or ""
                
                # Check file
                file_matched = any(f.lower() in rel_path.lower() for f in expected_files) if expected_files else False
                # Check symbol
                sym_matched = any(s.lower() == sym_name.lower() for s in expected_symbols) if expected_symbols else False
                
                if (expected_files and file_matched) or (expected_symbols and sym_matched):
                    appeared = True
                    break
            layer_appearance[name] = appeared

        results.append({
            "query_id": query_id,
            "query": raw_query,
            "expected_files": expected_files,
            "expected_symbols": expected_symbols,
            "entities": entities,
            "label_intent": label_intent,
            "reranker_intent": reranker_intent,
            "boost_labels": boost_labels,
            "layer_appearance": layer_appearance,
            "dense": format_layer_top10(dense, is_tuple_list=True),
            "lexical": format_layer_top10(lexical, is_tuple_list=True),
            "metadata": format_layer_top10(metadata, is_tuple_list=True),
            "exact": format_layer_top10(exact, is_tuple_list=True),
            "dependency": format_layer_top10(dependency, is_tuple_list=True),
            "merged": format_layer_top10(merged, is_tuple_list=False),
            "final": format_layer_top10(final_list, is_tuple_list=False)
        })

    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    run_analysis()
