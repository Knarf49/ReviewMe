def test_redis_reachable(redis_client):
    redis_client.set("k", "v")
    assert redis_client.get("k") == "v"
