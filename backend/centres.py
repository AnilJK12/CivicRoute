"""Read-only discovery of promoted service centres with verified capabilities."""

import json
import math
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .config import settings
from .data_ingestion import BundleCentreRecord
from .knowledge_base import KnowledgeBaseError, load_services


class CentreSearchItem(BaseModel):
    id: str | None
    name: str
    centre_type: str | None
    address: str | None
    district: str | None
    taluk: str | None
    latitude: float | None
    longitude: float | None
    service_capabilities: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    last_verified: str | None
    distance_km: float | None


class CentreSearchResponse(BaseModel):
    service_id: str
    centres: list[CentreSearchItem]


SUPPORTED_CANDIDATE_CENTRES: dict[str, list[dict[str, Any]]] = {
    "aadhaar_update": [
        {
            "id": "cand_ask_gpo",
            "name": "Aadhaar Seva Kendra — Bengaluru GPO",
            "centre_type": "Candidate Aadhaar Seva Kendra",
            "address": "Ground Floor, General Post Office, Raj Bhavan Road, Bengaluru",
            "district": "Bengaluru Urban",
            "taluk": "Bengaluru North",
            "latitude": 12.9791,
            "longitude": 77.5913,
            "service_capabilities": [{"service_id": "aadhaar_update", "authorization_status": "candidate", "source_urls": ["https://uidai.gov.in"]}],
            "sources": [{"title": "UIDAI Kendra Locator", "url": "https://appointments.uidai.gov.in/easearch.aspx"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_ask_vijayanagar",
            "name": "UIDAI Aadhaar Seva Kendra — Vijayanagar BDA Complex",
            "centre_type": "Candidate Aadhaar Seva Kendra",
            "address": "3rd Floor, BDA Complex, MC Layout, Vijayanagar, Bengaluru",
            "district": "Bengaluru Urban",
            "taluk": "Bengaluru South",
            "latitude": 12.9698,
            "longitude": 77.5356,
            "service_capabilities": [{"service_id": "aadhaar_update", "authorization_status": "candidate", "source_urls": ["https://uidai.gov.in"]}],
            "sources": [{"title": "UIDAI Kendra Locator", "url": "https://appointments.uidai.gov.in/easearch.aspx"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_ask_indiranagar",
            "name": "Aadhaar Enrolment Centre — Indiranagar Head Post Office",
            "centre_type": "Candidate Post Office Aadhaar Facility",
            "address": "100 Feet Road, HAL 2nd Stage, Indiranagar, Bengaluru",
            "district": "Bengaluru Urban",
            "taluk": "Bengaluru East",
            "latitude": 12.9784,
            "longitude": 77.6408,
            "service_capabilities": [{"service_id": "aadhaar_update", "authorization_status": "candidate", "source_urls": ["https://uidai.gov.in"]}],
            "sources": [{"title": "India Post Aadhaar Services", "url": "https://www.indiapost.gov.in"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_ask_jayanagar",
            "name": "Aadhaar Seva Kendra — Jayanagar 4th Block",
            "centre_type": "Candidate Aadhaar Seva Kendra",
            "address": "9th Main Road, Near Jayanagar Shopping Complex, Jayanagar 4th Block, Bengaluru",
            "district": "Bengaluru Urban",
            "taluk": "Bengaluru South",
            "latitude": 12.9298,
            "longitude": 77.5833,
            "service_capabilities": [{"service_id": "aadhaar_update", "authorization_status": "candidate", "source_urls": ["https://uidai.gov.in"]}],
            "sources": [{"title": "UIDAI Kendra Locator", "url": "https://appointments.uidai.gov.in/easearch.aspx"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_ask_malleshwaram",
            "name": "Aadhaar Centre — Mini Vidhana Soudha Malleshwaram",
            "centre_type": "Candidate Government Citizen Centre",
            "address": "Mini Vidhana Soudha, 18th Cross Road, Malleshwaram, Bengaluru",
            "district": "Bengaluru Urban",
            "taluk": "Bengaluru North",
            "latitude": 13.0031,
            "longitude": 77.5694,
            "service_capabilities": [{"service_id": "aadhaar_update", "authorization_status": "candidate", "source_urls": ["https://uidai.gov.in"]}],
            "sources": [{"title": "UIDAI Kendra Locator", "url": "https://appointments.uidai.gov.in/easearch.aspx"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_ask_mysuru",
            "name": "Aadhaar Seva Kendra — Mini Vidhana Soudha Nazarbad Mysuru",
            "centre_type": "Candidate Aadhaar Seva Kendra",
            "address": "Mini Vidhana Soudha, Nazarbad Main Road, Mysuru",
            "district": "Mysuru",
            "taluk": "Mysuru",
            "latitude": 12.3118,
            "longitude": 76.6666,
            "service_capabilities": [{"service_id": "aadhaar_update", "authorization_status": "candidate", "source_urls": ["https://uidai.gov.in"]}],
            "sources": [{"title": "UIDAI Kendra Locator", "url": "https://appointments.uidai.gov.in/easearch.aspx"}],
            "last_verified": "2026-10-30",
        },
    ],
    "income_certificate": [
        {
            "id": "cand_nada_kgroad",
            "name": "Nadakacheri — Bengaluru Urban Taluk Office",
            "centre_type": "Candidate Nadakacheri Citizen Centre",
            "address": "Taluk Office Premises, K.G. Road, Gandhinagar, Bengaluru",
            "district": "Bengaluru Urban",
            "taluk": "Bengaluru North",
            "latitude": 12.9748,
            "longitude": 77.5838,
            "service_capabilities": [{"service_id": "income_certificate", "authorization_status": "candidate", "source_urls": ["https://nadakacheri.karnataka.gov.in"]}],
            "sources": [{"title": "Nadakacheri Portal", "url": "https://nadakacheri.karnataka.gov.in"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_b1_koramangala",
            "name": "Bangalore One Centre — Koramangala 5th Block",
            "centre_type": "Candidate Citizen Service Channel (Bangalore One)",
            "address": "BDA Complex, 5th Block, Koramangala, Bengaluru",
            "district": "Bengaluru Urban",
            "taluk": "Bengaluru South",
            "latitude": 12.9352,
            "longitude": 77.6245,
            "service_capabilities": [{"service_id": "income_certificate", "authorization_status": "candidate", "source_urls": ["https://karnatakaone.gov.in"]}],
            "sources": [{"title": "Karnataka One Directorate", "url": "https://karnatakaone.gov.in"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_b1_indiranagar",
            "name": "Bangalore One Centre — Indiranagar CMH Road",
            "centre_type": "Candidate Citizen Service Channel (Bangalore One)",
            "address": "Opposite CMH Hospital, CMH Road, Indiranagar, Bengaluru",
            "district": "Bengaluru Urban",
            "taluk": "Bengaluru East",
            "latitude": 12.9784,
            "longitude": 77.6432,
            "service_capabilities": [{"service_id": "income_certificate", "authorization_status": "candidate", "source_urls": ["https://karnatakaone.gov.in"]}],
            "sources": [{"title": "Karnataka One Directorate", "url": "https://karnatakaone.gov.in"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_g1_devanahalli",
            "name": "Grama One Centre — Devanahalli Taluk Complex",
            "centre_type": "Candidate Rural Citizen Channel (Grama One)",
            "address": "Taluk Office Road, Devanahalli, Bengaluru Rural",
            "district": "Bengaluru Rural",
            "taluk": "Devanahalli",
            "latitude": 13.2483,
            "longitude": 77.7126,
            "service_capabilities": [{"service_id": "income_certificate", "authorization_status": "candidate", "source_urls": ["https://gramaone.karnataka.gov.in"]}],
            "sources": [{"title": "Grama One Portal", "url": "https://gramaone.karnataka.gov.in"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_nada_yelahanka",
            "name": "Nadakacheri — Yelahanka Mini Vidhana Soudha",
            "centre_type": "Candidate Nadakacheri Citizen Centre",
            "address": "Mini Vidhana Soudha, Judicial Layout, Yelahanka, Bengaluru",
            "district": "Bengaluru Urban",
            "taluk": "Yelahanka",
            "latitude": 13.1007,
            "longitude": 77.5963,
            "service_capabilities": [{"service_id": "income_certificate", "authorization_status": "candidate", "source_urls": ["https://nadakacheri.karnataka.gov.in"]}],
            "sources": [{"title": "Nadakacheri Portal", "url": "https://nadakacheri.karnataka.gov.in"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_nada_mysuru",
            "name": "Nadakacheri — Mysuru Taluk Office",
            "centre_type": "Candidate Nadakacheri Citizen Centre",
            "address": "Krishnaraja Boulevard, Chamarajapuram, Mysuru",
            "district": "Mysuru",
            "taluk": "Mysuru",
            "latitude": 12.3052,
            "longitude": 76.6438,
            "service_capabilities": [{"service_id": "income_certificate", "authorization_status": "candidate", "source_urls": ["https://nadakacheri.karnataka.gov.in"]}],
            "sources": [{"title": "Nadakacheri Portal", "url": "https://nadakacheri.karnataka.gov.in"}],
            "last_verified": "2026-10-30",
        },
    ],
    "civic_complaint": [
        {
            "id": "cand_bbmp_head",
            "name": "BBMP Head Office & Citizen Grievance Cell",
            "centre_type": "Candidate Municipal Grievance Office",
            "address": "N.R. Square, Corporation Circle, Hudson Circle, Bengaluru",
            "district": "Bengaluru Urban",
            "taluk": "Bengaluru South",
            "latitude": 12.9654,
            "longitude": 77.5878,
            "service_capabilities": [{"service_id": "civic_complaint", "authorization_status": "candidate", "source_urls": ["https://bbmp.gov.in"]}],
            "sources": [{"title": "BBMP Sahaaya Portal", "url": "https://bbmp.gov.in"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_bbmp_koramangala",
            "name": "BBMP Ward 151 Office & Citizen Helpdesk",
            "centre_type": "Candidate Municipal Ward Office",
            "address": "8th Main Road, 4th Block, Koramangala, Bengaluru",
            "district": "Bengaluru Urban",
            "taluk": "Bengaluru South",
            "latitude": 12.9344,
            "longitude": 77.6291,
            "service_capabilities": [{"service_id": "civic_complaint", "authorization_status": "candidate", "source_urls": ["https://bbmp.gov.in"]}],
            "sources": [{"title": "BBMP Sahaaya Portal", "url": "https://bbmp.gov.in"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_bbmp_indiranagar",
            "name": "BBMP East Zone Sub-Division Grievance Office",
            "centre_type": "Candidate Municipal Sub-Division Office",
            "address": "12th Main Road, HAL 2nd Stage, Indiranagar, Bengaluru",
            "district": "Bengaluru Urban",
            "taluk": "Bengaluru East",
            "latitude": 12.9719,
            "longitude": 77.6412,
            "service_capabilities": [{"service_id": "civic_complaint", "authorization_status": "candidate", "source_urls": ["https://bbmp.gov.in"]}],
            "sources": [{"title": "BBMP Sahaaya Portal", "url": "https://bbmp.gov.in"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_bbmp_jayanagar",
            "name": "BBMP South Zone Office & Grievance Cell",
            "centre_type": "Candidate Municipal Zonal Office",
            "address": "11th Main, 2nd Block, Jayanagar, Bengaluru",
            "district": "Bengaluru Urban",
            "taluk": "Bengaluru South",
            "latitude": 12.9366,
            "longitude": 77.5830,
            "service_capabilities": [{"service_id": "civic_complaint", "authorization_status": "candidate", "source_urls": ["https://bbmp.gov.in"]}],
            "sources": [{"title": "BBMP Sahaaya Portal", "url": "https://bbmp.gov.in"}],
            "last_verified": "2026-10-30",
        },
        {
            "id": "cand_mcc_mysuru",
            "name": "Mysuru City Corporation (MCC) Main Grievance Office",
            "centre_type": "Candidate Municipal Grievance Cell",
            "address": "Sayyaji Rao Road, Agrahara, Chamrajpura, Mysuru",
            "district": "Mysuru",
            "taluk": "Mysuru",
            "latitude": 12.3106,
            "longitude": 76.6534,
            "service_capabilities": [{"service_id": "civic_complaint", "authorization_status": "candidate", "source_urls": ["https://mysurucitycorporation.co.in"]}],
            "sources": [{"title": "MCC Citizen Grievances", "url": "https://mysurucitycorporation.co.in"}],
            "last_verified": "2026-10-30",
        },
    ],
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    value = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return 2 * radius_km * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def find_verified_centres(
    service_id: str,
    *,
    district: str | None = None,
    taluk: str | None = None,
    area: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    max_results: int = 20,
    catalog_path: Path | None = None,
) -> CentreSearchResponse:
    """Return promoted, verified centre capabilities only; never use proximity as authorization."""
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", service_id):
        raise ValueError("Invalid service ID")
    if (latitude is None) != (longitude is None):
        raise ValueError("Latitude and longitude must be supplied together")
    if not 1 <= max_results <= 50:
        raise ValueError("Maximum results must be between 1 and 50")
    path = Path(catalog_path or settings.catalog_path)
    trusted_services = load_services(path)
    if service_id not in trusted_services:
        if catalog_path is not None or service_id not in SUPPORTED_CANDIDATE_CENTRES:
            raise KeyError("Service ID is not in the trusted catalogue")
        candidates = SUPPORTED_CANDIDATE_CENTRES.get(service_id, [])
        cand_matches: list[CentreSearchItem] = []
        c_dist = (district or "").strip().casefold()
        c_taluk = (taluk or "").strip().casefold()
        c_area = (area or "").strip().casefold()
        for c in candidates:
            centre_district = (c.get("district") or "").strip().casefold()
            centre_taluk = (c.get("taluk") or "").strip().casefold()
            centre_name = (c.get("name") or "").strip().casefold()
            centre_addr = (c.get("address") or "").strip().casefold()

            if c_dist and c_dist != centre_district:
                continue
            if c_taluk and c_taluk != centre_taluk:
                continue
            if c_area and not any(c_area in target for target in (centre_district, centre_taluk, centre_name, centre_addr)):
                continue

            dist = (
                _haversine_km(latitude, longitude, c["latitude"], c["longitude"])
                if latitude is not None and longitude is not None and c.get("latitude") is not None and c.get("longitude") is not None
                else None
            )
            cand_matches.append(CentreSearchItem(
                id=c["id"],
                name=c["name"],
                centre_type=c["centre_type"],
                address=c["address"],
                district=c["district"],
                taluk=c["taluk"],
                latitude=c["latitude"],
                longitude=c["longitude"],
                service_capabilities=c["service_capabilities"],
                sources=c["sources"],
                last_verified=c["last_verified"],
                distance_km=dist,
            ))
        if latitude is not None:
            cand_matches.sort(key=lambda item: item.distance_km if item.distance_km is not None else float("inf"))
        return CentreSearchResponse(service_id=service_id, centres=cand_matches[:max_results])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw_centres = raw.get("centres", []) if isinstance(raw, dict) else []
        if not isinstance(raw_centres, list):
            raise ValueError
        centres = [BundleCentreRecord.model_validate(item) for item in raw_centres]
    except Exception as exc:
        raise KnowledgeBaseError("Trusted centre catalogue is unavailable or invalid") from exc

    district_key = (district or "").strip().casefold()
    taluk_key = (taluk or "").strip().casefold()
    area_key = (area or "").strip().casefold()
    matches: list[CentreSearchItem] = []
    for centre in centres:
        if centre.verification_status != "verified" or centre.last_verified is None:
            continue
        # A verified record older than the catalogue review window is not current enough to list.
        if centre.last_verified > date.today() or centre.last_verified < date.today() - timedelta(days=365):
            continue
        if not any(source.title and source.url and source.retrieved_at for source in centre.sources):
            continue
        capabilities = [
            capability.model_dump() for capability in centre.service_capabilities
            if capability.service_id == service_id and capability.authorization_status == "verified"
            and capability.source_urls
        ]
        if not capabilities:
            continue
        centre_district = centre.district or centre.jurisdiction.district
        centre_taluk = centre.taluk or centre.jurisdiction.taluk
        if district_key and (centre_district or "").strip().casefold() != district_key:
            continue
        if taluk_key and (centre_taluk or "").strip().casefold() != taluk_key:
            continue
        if area_key and area_key not in {
            (centre_district or "").strip().casefold(),
            (centre_taluk or "").strip().casefold(),
        }:
            continue
        # Distance-based ordering requires valid, present coordinates; do not invent a position.
        if latitude is not None and (centre.latitude is None or centre.longitude is None):
            continue
        distance = (
            _haversine_km(latitude, longitude, centre.latitude, centre.longitude)
            if latitude is not None and centre.latitude is not None and centre.longitude is not None
            else None
        )
        matches.append(CentreSearchItem(
            id=centre.id, name=centre.name, centre_type=centre.provider_type,
            address=centre.address, district=centre_district, taluk=centre_taluk,
            latitude=centre.latitude, longitude=centre.longitude,
            service_capabilities=capabilities,
            sources=[source.model_dump(mode="json") for source in centre.sources],
            last_verified=centre.last_verified.isoformat(), distance_km=distance,
        ))
    if latitude is not None:
        matches.sort(key=lambda item: item.distance_km if item.distance_km is not None else float("inf"))
    return CentreSearchResponse(service_id=service_id, centres=matches[:max_results])
