"""Provider matching requires explicit, sourced service authorization."""

from backend.provider_matching import verified_capable_providers


def test_nearby_but_unverified_candidate_is_excluded_before_distance_ranking():
    candidates = [
        {
            "provider_id": "nearby-unverified",
            "provider_name": "Synthetic nearby provider",
            "distance_km": 0.2,
            "service_capabilities": [{
                "service_id": "sample_access", "status": "unverified",
                "sources": [{"title": "Synthetic source", "url": "https://example.test/source"}],
            }],
        },
        {
            "provider_id": "far-authorized",
            "provider_name": "Synthetic authorized provider",
            "distance_km": 8.0,
            "service_capabilities": [{
                "service_id": "sample_access", "status": "verified",
                "sources": [{"title": "Synthetic source", "url": "https://example.test/verified"}],
            }],
        },
        {
            "provider_id": "other-service",
            "provider_name": "Synthetic different-service provider",
            "distance_km": 0.1,
            "service_capabilities": [{
                "service_id": "different_service", "status": "verified",
                "sources": [{"title": "Synthetic source", "url": "https://example.test/other"}],
            }],
        },
    ]
    matches = verified_capable_providers(candidates, "sample_access")
    assert [match.provider_id for match in matches] == ["far-authorized"]


def test_provider_capability_requires_source_provenance():
    candidate = {
        "provider_id": "missing-source",
        "provider_name": "Synthetic provider",
        "distance_km": 1.0,
        "service_capabilities": [{
            "service_id": "sample_access", "status": "verified", "sources": [],
        }],
    }
    assert verified_capable_providers([candidate], "sample_access") == []
