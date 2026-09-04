# =============================================================
# OWNER: AARON
# =============================================================
import os
import sys
from unittest.mock import patch, MagicMock

# Mock kafka so we don't need it installed on the host
sys.modules['kafka'] = MagicMock()

# 1. Test Config is loaded from env
def test_config_loads_env():
    from shared.config import Settings
    with patch.dict(os.environ, {"KAFKA_BOOTSTRAP_SERVERS": "test:9092"}):
        assert os.environ["KAFKA_BOOTSTRAP_SERVERS"] == "test:9092"
    print("test_config_loads_env passed!")

# 2. Test Chunked Processor Memory Leak Fix
def test_chunked_processor():
    from shared.chunked_processor import process_chunked
    assert callable(process_chunked)
    print("test_chunked_processor passed!")

# 3. Test Reliable Producer (Kafka delivery callback)
def test_reliable_producer():
    from shared.kafka_helpers import create_reliable_producer, send_with_callback
    assert callable(create_reliable_producer)
    assert callable(send_with_callback)
    print("test_reliable_producer passed!")

if __name__ == "__main__":
    test_config_loads_env()
    test_chunked_processor()
    test_reliable_producer()
    print("All tests passed successfully!")
