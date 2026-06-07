import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag_ingestion.models.file import FileRecord
from rag_ingestion.stages.parser import parse_file
from rag_ingestion.utils.counters import PipelineCounters

def test_parser():
    file_record = FileRecord(
        path="/home/arch/DEV/CodeSeek/backend/scratch/test_code.jsx",
        relative_path="scratch/test_code.jsx",
        extension=".jsx",
        size_bytes=100,
        language="javascript",
    )
    
    counters = PipelineCounters()
    parsed_file = parse_file(file_record, counters)
    
    print(f"Parse status: {parsed_file.parse_status}")
    print(f"Imports: {parsed_file.imports}")
    print("\nSymbols found:")
    for symbol in parsed_file.symbols:
        print(f"- Name: {symbol.symbol_name}")
        print(f"  Type: {symbol.symbol_type}")
        print(f"  Parent: {symbol.parent_symbol or 'None'}")
        print(f"  Signature: {symbol.signature}")
        print(f"  Parameters: {symbol.parameters}")
        print(f"  Lines: {symbol.start_line} to {symbol.end_line}")
        print(f"  Calls: {symbol.calls}")
        print("-" * 40)

if __name__ == "__main__":
    test_parser()
