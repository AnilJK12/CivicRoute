import { useEffect, useRef, useState } from "react";
import { importLibrary, setOptions } from "@googlemaps/js-api-loader";

type CentreSource = { title: string | null; url: string | null };
type Centre = {
  id: string | null;
  name: string;
  centre_type: string | null;
  address: string | null;
  district: string | null;
  taluk: string | null;
  latitude: number | null;
  longitude: number | null;
  service_capabilities: { service_id: string; authorization_status: string }[];
  sources: CentreSource[];
  last_verified: string | null;
  distance_km: number | null;
};

let mapsLibraryPromise: Promise<google.maps.MapsLibrary> | null = null;

function loadMapsLibrary(apiKey: string): Promise<google.maps.MapsLibrary> {
  if (!mapsLibraryPromise) {
    setOptions({ key: apiKey });
    mapsLibraryPromise = importLibrary("maps") as Promise<google.maps.MapsLibrary>;
  }
  return mapsLibraryPromise;
}

function createInfoContent(centre: Centre): HTMLElement {
  const content = document.createElement("div");
  content.className = "max-w-xs space-y-1 p-1 text-sm";
  const addText = (tag: string, text: string) => {
    const element = document.createElement(tag);
    element.textContent = text;
    content.append(element);
  };

  addText("strong", centre.name);
  if (centre.centre_type) addText("p", centre.centre_type);
  if (centre.address) addText("p", centre.address);
  const locality = [centre.taluk, centre.district].filter(Boolean).join(", ");
  if (locality) addText("p", locality);
  if (centre.distance_km !== null) addText("p", `${centre.distance_km.toFixed(1)} km away`);
  if (centre.last_verified) addText("p", `Verified ${centre.last_verified}`);
  for (const source of centre.sources) {
    if (!source.url) continue;
    const link = document.createElement("a");
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = source.title || "Official source";
    content.append(link);
  }
  return content;
}

function isCentre(value: unknown): value is Centre {
  if (typeof value !== "object" || value === null) return false;
  const centre = value as Record<string, unknown>;
  return typeof centre.name === "string"
    && (centre.latitude === null || typeof centre.latitude === "number")
    && (centre.longitude === null || typeof centre.longitude === "number")
    && (centre.distance_km === null || typeof centre.distance_km === "number")
    && Array.isArray(centre.sources)
    && Array.isArray(centre.service_capabilities);
}

export function NearbyCentresMap({
  searchArea = "",
  serviceId,
}: {
  searchArea?: string;
  serviceId?: string | null;
}) {
  const mapElement = useRef<HTMLDivElement>(null);
  const [map, setMap] = useState<google.maps.Map | null>(null);
  const [mapMessage, setMapMessage] = useState("Loading map…");
  const [dataMessage, setDataMessage] = useState("");
  const [centres, setCentres] = useState<Centre[]>([]);
  const [loading, setLoading] = useState(false);
  const [coordinates, setCoordinates] = useState<{ latitude: number; longitude: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    const apiKey = import.meta.env.VITE_GOOGLE_MAPS_API_KEY?.trim();

    if (!apiKey) {
      setMapMessage("Map unavailable. Configure VITE_GOOGLE_MAPS_API_KEY in frontend/.env.local.");
      return () => { cancelled = true; };
    }

    loadMapsLibrary(apiKey)
      .then(({ Map }) => {
        if (cancelled || !mapElement.current) return;
        const instance = new Map(mapElement.current, {
          center: { lat: 0, lng: 0 },
          zoom: 2,
          clickableIcons: false,
        });
        setMap(instance);
        setMapMessage("");
      })
      .catch(() => {
        if (!cancelled) setMapMessage("Map could not be loaded. Check the Maps configuration and try again.");
      });

    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!serviceId) {
      setCentres([]);
      setDataMessage("Submit a request for a supported service to find verified centres.");
      return;
    }

    const controller = new AbortController();
    const apiBase = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
    const params = new URLSearchParams({ service_id: serviceId, max_results: "20" });
    if (searchArea.trim()) params.set("area", searchArea.trim());
    if (coordinates) {
      params.set("latitude", String(coordinates.latitude));
      params.set("longitude", String(coordinates.longitude));
    }

    setLoading(true);
    setDataMessage("");
    setCentres([]);
    fetch(`${apiBase}/centres?${params.toString()}`, { signal: controller.signal })
      .then(async (response) => {
        const body: unknown = await response.json();
        if (!response.ok) throw new Error("Centre data could not be loaded.");
        if (typeof body !== "object" || body === null || !Array.isArray((body as { centres?: unknown }).centres)) {
          throw new Error("Centre data response was invalid.");
        }
        const records = (body as { centres: unknown[] }).centres;
        if (!records.every(isCentre)) throw new Error("Centre data response was invalid.");
        return records;
      })
      .then((items) => {
        setCentres(items);
        setDataMessage(items.length ? "" : "No verified centres were found for this service and area.");
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setDataMessage(error instanceof Error ? error.message : "Centre data could not be loaded.");
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });

    return () => controller.abort();
  }, [coordinates, searchArea, serviceId]);

  useEffect(() => {
    if (!map) return;
    const markers: google.maps.Marker[] = [];
    const infoWindow = new google.maps.InfoWindow();
    const plottable = centres.filter(
      (centre) => centre.latitude !== null && centre.longitude !== null,
    );

    if (plottable.length > 0) {
      const bounds = new google.maps.LatLngBounds();
      plottable.forEach((centre) => {
        const position = { lat: centre.latitude!, lng: centre.longitude! };
        const marker = new google.maps.Marker({ map, position, title: centre.name });
        marker.addListener("click", () => {
          infoWindow.setContent(createInfoContent(centre));
          infoWindow.open({ map, anchor: marker });
        });
        markers.push(marker);
        bounds.extend(position);
      });
      map.fitBounds(bounds);
    }

    return () => {
      infoWindow.close();
      markers.forEach((marker) => marker.setMap(null));
    };
  }, [centres, map]);

  const nonMappableCount = centres.filter(
    (centre) => centre.latitude === null || centre.longitude === null,
  ).length;

  function useCurrentLocation() {
    if (!navigator.geolocation) {
      setDataMessage("Location is not available in this browser.");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      ({ coords: current }) => setCoordinates({ latitude: current.latitude, longitude: current.longitude }),
      () => setDataMessage("Location could not be retrieved. Centres are shown without distance."),
      { timeout: 10000, maximumAge: 60000 },
    );
  }

  return (
    <section id="nearby-centres-map" className="mt-4 w-full text-left" aria-label="Nearby centres map">
      <div ref={mapElement} className="h-80 w-full overflow-hidden rounded-xl border bg-muted" />
      <button
        type="button"
        onClick={useCurrentLocation}
        className="mt-2 rounded-lg border px-3 py-2 text-sm hover:bg-muted"
      >
        {coordinates ? "Refresh distance from my location" : "Use my location to show distance"}
      </button>
      {mapMessage && <p className="mt-2 text-sm text-muted-foreground" role="status">{mapMessage}</p>}
      <p className="mt-2 text-sm text-muted-foreground" role="status" aria-live="polite">
        {loading ? "Loading verified centres…" : dataMessage || `${centres.length} verified centre${centres.length === 1 ? "" : "s"} found.`}
        {nonMappableCount > 0 && ` ${nonMappableCount} verified centre${nonMappableCount === 1 ? "" : "s"} have no coordinates and cannot be shown on the map.`}
      </p>
    </section>
  );
}
