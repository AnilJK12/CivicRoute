import { createFileRoute } from "@tanstack/react-router";
import { useRef, useState } from "react";
import {
  ArrowRight, CheckCircle2, Circle, FileText, Globe, Landmark,
  LoaderCircle, MapPin, Mic, Search, ShieldCheck, X, AlertTriangle,
  Navigation, ExternalLink, Building2, Check, Compass
} from "lucide-react";
import { NearbyCentresMap } from "../components/NearbyCentresMap";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "CivicRoute — Karnataka Public Service Navigator" },
      {
        name: "description",
        content:
          "AI-powered Karnataka government-service navigation assistant. Find services, documents, procedures and nearby service centres.",
      },
    ],
  }),
  component: Index,
});

const WORKFLOW = [
  ["Understand your request", "Identify the service and relevant department."],
  ["Clarify your needs", "Ask follow-up questions if details are missing."],
  ["AI official-source research", "Search authoritative Karnataka & central government portals."],
  ["Verify requirements", "Check probable documents, fees, and online options."],
  ["Prepare your practical guide", "Provide step-by-step procedures and official source links."],
  ["Discover service centres", "Find candidate centres and navigate via Google Maps."],
];

type Source = { title: string; url?: string };
type DocumentItem = string | { name?: string; description?: string; required?: boolean; notes?: string; status?: string; note?: string };
type ResearchItem = {
  department: string;
  identified_service: string;
  information_status: "Officially verified" | "Probable" | "Not found";
  probable_documents: { text: string; source_title: string; source_url: string; information_status?: string }[];
  probable_steps: { text: string; source_title: string; source_url: string; information_status?: string }[];
  online_option?: { available?: boolean | null; url?: string | null; status?: string };
  offline_option?: string | null;
  sources: { title: string; url: string; excerpt?: string }[];
  note?: string | null;
};
type AskResponse = {
  status: "ok" | "unsupported" | "clarification_needed" | string;
  service_id?: string;
  service_name?: string | null;
  language?: "en" | "kn";
  message?: string;
  question?: string;
  clarification_question?: string;
  location?: string;
  missing_fields?: string[];
  web_results?: { title: string; url: string; content?: string; domain?: string }[];
  steps?: string[];
  documents?: DocumentItem[];
  eligibility?: { status: "verified" | "not_verified" | "verified_unavailable"; criteria: string[]; note?: string | null };
  fee?: { amount?: number | null; currency?: string; status?: string; note?: string | null };
  online_option?: { available?: boolean | null; url?: string | null; status?: string };
  office_type?: string | null;
  grievance?: string | null;
  sources?: Source[];
  last_verified?: string | null;
  warnings?: string[];
  official_portal_url?: string | null;
  suggested_services?: string[];
  research?: ResearchItem | null;
};

type Centre = {
  id: string | null;
  name: string;
  centre_type: string | null;
  address: string | null;
  district: string | null;
  taluk: string | null;
  latitude: number | null;
  longitude: number | null;
  service_capabilities?: { service_id: string; authorization_status: string }[];
  sources?: { title: string | null; url: string | null }[];
  last_verified?: string | null;
  distance_km?: number | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isAskResponse(value: unknown): value is AskResponse {
  return isRecord(value) && typeof value.status === "string";
}

function Index() {
  const [language, setLanguage] = useState<"en" | "kn">("en");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<AskResponse | null>(null);
  const [completedStages, setCompletedStages] = useState<number[]>([]);
  const [history, setHistory] = useState<{ role: "citizen" | "assistant"; content: string }[]>([]);
  const guideRef = useRef<HTMLElement>(null);
  const discoveryRef = useRef<HTMLElement>(null);

  // Dynamic nearby service centre discovery state
  const [discoveryActive, setDiscoveryActive] = useState(false);
  const [candidateCentres, setCandidateCentres] = useState<Centre[]>([]);
  const [centresLoading, setCentresLoading] = useState(false);
  const [centresError, setCentresError] = useState("");
  const [userCoords, setUserCoords] = useState<{ latitude: number; longitude: number } | null>(null);
  const [localityText, setLocalityText] = useState("");
  const [selectedCentre, setSelectedCentre] = useState<Centre | null>(null);
  const [showDirectionsModal, setShowDirectionsModal] = useState(false);
  const [showInteractiveMap, setShowInteractiveMap] = useState(false);

  // Helper to resolve clean service ID for /centres query
  function getResolvedServiceId(): string {
    if (result?.service_id && result.service_id !== "unknown") return result.service_id;
    const combined = `${result?.research?.identified_service || ""} ${result?.service_name || ""} ${query}`.toLowerCase();
    if (combined.includes("aadhaar") || combined.includes("uidai") || combined.includes("aadhar")) return "aadhaar_update";
    if (combined.includes("income") || combined.includes("revenue") || combined.includes("caste") || combined.includes("residence")) return "income_certificate";
    if (combined.includes("civic") || combined.includes("complaint") || combined.includes("grievance") || combined.includes("pothole") || combined.includes("garbage")) return "civic_complaint";
    return "aadhaar_update";
  }

  async function submitRequest(text = query) {
    const question = text.trim();
    if (!question || loading) return;

    setLoading(true);
    setError("");
    setResult(null);
    setDiscoveryActive(false);
    setSelectedCentre(null);
    setShowDirectionsModal(false);
    setCandidateCentres([]);
    setCompletedStages([1]);

    const nextHistory = [...history, { role: "citizen" as const, content: question }];
    setHistory(nextHistory);

    try {
      const apiBase = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
      const response = await fetch(`${apiBase}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, language }),
      });

      const data: unknown = await response.json();
      if (!response.ok) {
        const errorMessage = isRecord(data) && typeof data.message === "string"
          ? data.message
          : isRecord(data) && typeof data.detail === "string"
            ? data.detail
            : "Unable to process your request.";
        throw new Error(errorMessage);
      }
      if (!isAskResponse(data)) throw new Error("The backend returned an unexpected response.");

      setResult(data);
      const isComplete = data.status === "ok" || (data.status === "unsupported" && Boolean(data.research));
      setCompletedStages(isComplete ? [1, 2, 3, 4, 5, 6] : [1, 2, 3]);

      if (data.status === "clarification_needed" && (data.clarification_question || data.question)) {
        setHistory((previous) => [
          ...previous,
          { role: "assistant", content: (data.clarification_question || data.question)! },
        ]);
      }

      setQuery("");
      setTimeout(() => guideRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  // Dynamic nearby service centre discovery
  async function discoverCentres(coords = userCoords, area = localityText) {
    const serviceId = getResolvedServiceId();
    setCentresLoading(true);
    setCentresError("");
    setDiscoveryActive(true);

    try {
      const apiBase = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
      const params = new URLSearchParams({ service_id: serviceId, max_results: "20" });
      if (area.trim()) params.set("area", area.trim());
      if (coords) {
        params.set("latitude", String(coords.latitude));
        params.set("longitude", String(coords.longitude));
      }

      const response = await fetch(`${apiBase}/centres?${params.toString()}`);
      const data: unknown = await response.json();
      if (!response.ok) throw new Error("Failed to discover nearby service centres.");
      if (isRecord(data) && Array.isArray(data.centres)) {
        const found = data.centres as Centre[];
        setCandidateCentres(found);
        if (found.length === 0) {
          setCentresError(language === "kn"
            ? "ಈ ಪ್ರದೇಶದಲ್ಲಿ ಯಾವುದೇ ಸೇವಾ ಕೇಂದ್ರಗಳು ಕಂಡುಬಂದಿಲ್ಲ. ದಯವಿಟ್ಟು ಬೇರೆ ಜಿಲ್ಲೆ ಅಥವಾ ತಾಲ್ಲೂಕನ್ನು ನಮೂದಿಸಿ."
            : "No candidate centres found for this area. Try searching another district or taluk.");
        }
      }
      setTimeout(() => discoveryRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
    } catch (e) {
      setCentresError(e instanceof Error ? e.message : "Could not load service centres.");
    } finally {
      setCentresLoading(false);
    }
  }

  // Request browser location permission
  function handleUseBrowserLocation() {
    if (!navigator.geolocation) {
      setCentresError("Location is not supported in this browser. Please enter your district or area manually.");
      void discoverCentres(null, localityText);
      return;
    }
    setCentresLoading(true);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const coords = { latitude: position.coords.latitude, longitude: position.coords.longitude };
        setUserCoords(coords);
        void discoverCentres(coords, localityText);
      },
      (geoError) => {
        console.warn("Geolocation denied or failed:", geoError);
        setCentresError("Location permission denied. Enter your district or taluk manually below.");
        setCentresLoading(false);
        setDiscoveryActive(true);
      },
      { timeout: 10000, maximumAge: 60000 }
    );
  }

  // Centre selection & directions flow
  function handleSelectCentre(centre: Centre) {
    setSelectedCentre(centre);
    setShowDirectionsModal(true);
  }

  // Open Google Maps directions URL
  function handleOpenGoogleMaps() {
    if (!selectedCentre) return;
    const dest = selectedCentre.latitude && selectedCentre.longitude
      ? `${selectedCentre.latitude},${selectedCentre.longitude}`
      : `${selectedCentre.name}, ${selectedCentre.address || ""}`;
    const destinationEncoded = encodeURIComponent(dest);
    const mapsUrl = `https://www.google.com/maps/dir/?api=1&destination=${destinationEncoded}&travelmode=driving`;
    window.open(mapsUrl, "_blank", "noopener,noreferrer");
    setShowDirectionsModal(false);
  }

  function startVoiceInput() {
    const browser = window as Window & {
      SpeechRecognition?: new () => any;
      webkitSpeechRecognition?: new () => any;
    };
    const SpeechRecognition = browser.SpeechRecognition || browser.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setError("Voice input is not supported in this browser. Please type your request.");
      return;
    }
    const recognition = new SpeechRecognition();
    recognition.lang = language === "kn" ? "kn-IN" : "en-IN";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onstart = () => setListening(true);
    recognition.onend = () => setListening(false);
    recognition.onerror = () => {
      setListening(false);
      setError("Voice input failed. Please type your request.");
    };
    recognition.onresult = (event: any) => {
      const transcript = event.results[0][0].transcript;
      setQuery((old) => old ? `${old} ${transcript}` : transcript);
    };
    recognition.start();
  }

  function documentLabel(item: DocumentItem) {
    if (typeof item === "string") return item;
    return [item.name, item.description, item.notes || item.note].filter(Boolean).join(" — ");
  }

  // Has valid guide content (either ok response or research payload)
  const hasGuideContent = result && (result.status === "ok" || Boolean(result.research));
  const identifiedDepartment = result?.research?.department || (result?.status === "ok" ? "Karnataka Public Service" : null);
  const identifiedServiceName = result?.research?.identified_service || result?.service_name || (result?.status === "ok" ? "Public Service Guidance" : null);
  const infoStatus = result?.research?.information_status || (result?.status === "ok" ? "Officially verified" : null);

  const displayedSteps = result?.research?.probable_steps?.length
    ? result.research.probable_steps.map((s) => s.text)
    : result?.steps || [];

  type UnifiedDoc = { name: string; note?: string; status: string };

  const displayedDocs: UnifiedDoc[] = result?.research?.probable_documents?.length
    ? result.research.probable_documents.map((d) => ({
        name: d.text,
        note: d.source_title ? `Source: ${d.source_title}` : undefined,
        status: "required",
      }))
    : (result?.documents || []).map((doc) => {
        if (typeof doc === "string") return { name: doc, status: "required" };
        return {
          name: doc.name || "",
          note: doc.note || doc.notes || doc.description || undefined,
          status: doc.status || (doc.required ? "required" : "commonly_requested"),
        };
      });

  const onlineUrl = result?.research?.online_option?.url || result?.online_option?.url;
  const offlineOption = result?.research?.offline_option || result?.office_type;
  const officialSources = (result?.research?.sources && result.research.sources.length > 0)
    ? result.research.sources
    : result?.sources || [];

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Header */}
      <header className="sticky top-0 z-30 border-b bg-background/95 backdrop-blur">
        <nav className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4">
          <a href="#top" className="flex items-center gap-2 text-xl font-bold">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-primary text-primary-foreground shadow-sm">
              <Landmark className="h-5 w-5" />
            </span>
            CivicRoute
          </a>
          <div className="flex items-center gap-4 text-sm">
            <a href="#workflow" className="hidden hover:underline sm:block">How it works</a>
            <a href="#about" className="hidden hover:underline sm:block">About</a>
            <label className="flex items-center gap-2">
              <span className="sr-only">Language</span>
              <select
                aria-label="Language"
                value={language}
                onChange={(e) => setLanguage(e.target.value as "en" | "kn")}
                className="rounded-lg border bg-background px-3 py-1.5 font-medium text-sm shadow-sm"
              >
                <option value="en">English</option>
                <option value="kn">ಕನ್ನಡ (Kannada)</option>
              </select>
            </label>
          </div>
        </nav>
      </header>

      <main id="top">
        {/* Hero Section */}
        <section className="border-b bg-gradient-to-b from-primary/10 to-background">
          <div className="mx-auto max-w-4xl px-4 py-16 text-center sm:py-24">
            <span className="inline-flex items-center gap-2 rounded-full border bg-background px-3.5 py-1.5 text-xs font-semibold shadow-sm">
              <ShieldCheck className="h-4 w-4 text-primary" /> Karnataka Public Service Navigator
            </span>
            <h1 className="mt-6 text-4xl font-extrabold tracking-tight sm:text-6xl">
              Government services,<span className="block text-primary">made simple.</span>
            </h1>
            <p className="mx-auto mt-5 max-w-2xl text-muted-foreground sm:text-lg">
              Ask your question in plain words. CivicRoute identifies the right department, extracts probable required documents, explains the procedure, and guides you to the nearest service centre.
            </p>

            {/* Quick Example Prompts */}
            <div className="mt-6 flex flex-wrap justify-center gap-2 text-xs">
              <span className="text-muted-foreground self-center">Try asking:</span>
              <button
                type="button"
                onClick={() => { setQuery("I want to update my Aadhaar address. What documents do I need?"); }}
                className="rounded-full border bg-background px-3 py-1 hover:border-primary hover:text-primary transition-colors"
              >
                "Update Aadhaar address documents"
              </button>
              <button
                type="button"
                onClick={() => { setQuery("How can I apply for an income certificate in Karnataka?"); }}
                className="rounded-full border bg-background px-3 py-1 hover:border-primary hover:text-primary transition-colors"
              >
                "Apply for income certificate"
              </button>
              <button
                type="button"
                onClick={() => { setQuery("How do I file a civic complaint?"); }}
                className="rounded-full border bg-background px-3 py-1 hover:border-primary hover:text-primary transition-colors"
              >
                "File a civic complaint"
              </button>
            </div>

            {/* Citizen Request Form */}
            <form
              className="mx-auto mt-6 max-w-3xl rounded-2xl border bg-background p-4 text-left shadow-lg"
              onSubmit={(e) => { e.preventDefault(); void submitRequest(); }}
            >
              <label htmlFor="citizen-request" className="mb-2 block px-1 text-sm font-semibold">
                What government service or document do you need assistance with?
              </label>
              <textarea
                id="citizen-request"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={language === "kn"
                  ? "ಉದಾ: ನನ್ನ ಆಧಾರ್ ವಿಳಾಸ ತಿದ್ದುಪಡಿ ಮಾಡಲು ಯಾವ ದಾಖಲೆಗಳು ಬೇಕು?"
                  : "E.g. I want to update my Aadhaar address. What documents do I need?"}
                rows={3}
                className="w-full resize-y rounded-xl border bg-transparent p-3 outline-none focus:ring-2 focus:ring-primary text-base"
              />

              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <button
                  type="button"
                  onClick={startVoiceInput}
                  disabled={loading || listening}
                  className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50"
                >
                  <Mic className="h-4 w-4 text-primary" /> {listening ? "Listening..." : "Speak your request"}
                </button>
                <button
                  type="submit"
                  disabled={loading || !query.trim()}
                  className="inline-flex items-center gap-2 rounded-xl bg-primary px-6 py-3 font-semibold text-primary-foreground shadow hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                  {loading ? "Researching..." : "Find my service"}
                  {!loading && <ArrowRight className="h-4 w-4" />}
                </button>
              </div>
            </form>

            <p className="mt-4 text-xs text-muted-foreground">
              CivicRoute respects your privacy. Avoid entering Aadhaar numbers, OTPs, or passwords.
            </p>
            {error && <div role="alert" className="mx-auto mt-5 max-w-3xl rounded-xl border border-red-300 bg-red-50 p-4 text-left text-sm text-red-800">{error}</div>}
          </div>
        </section>

        {/* Workflow Stages Indicator */}
        <section id="workflow" className="mx-auto max-w-6xl px-4 py-12">
          <div className="max-w-2xl">
            <p className="text-sm font-semibold uppercase tracking-wide text-primary">Behind every request</p>
            <h2 className="mt-2 text-2xl sm:text-3xl font-bold">End-to-End Citizen Navigation Journey</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              From natural-language inquiry to official AI research, document checklist, and GPS navigation to your nearest service centre.
            </p>
          </div>
          <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {WORKFLOW.map(([title, description], i) => {
              const done = completedStages.includes(i + 1);
              return (
                <article key={title} className={`rounded-xl border p-4 transition-all ${done ? "border-primary/50 bg-primary/5" : "bg-card"}`}>
                  <div className="flex items-center justify-between">
                    <span className={`grid h-8 w-8 place-items-center rounded-lg font-bold text-sm ${done ? "bg-primary text-primary-foreground" : "bg-primary/10 text-primary"}`}>{i + 1}</span>
                    {done ? <CheckCircle2 className="h-5 w-5 text-emerald-600" /> : <Circle className="h-5 w-5 text-muted-foreground/40" />}
                  </div>
                  <h3 className="mt-3 font-bold text-sm">{title}</h3>
                  <p className="mt-1 text-xs text-muted-foreground">{description}</p>
                </article>
              );
            })}
          </div>
        </section>

        {/* Results & Guidance Section */}
        <section ref={guideRef} id="guidance" className="mx-auto max-w-5xl scroll-mt-24 px-4 pb-16">
          {loading && (
            <div className="rounded-2xl border p-8 text-center bg-card shadow-sm">
              <LoaderCircle className="mx-auto h-8 w-8 animate-spin text-primary" />
              <h2 className="mt-4 text-xl font-bold">Researching Official Karnataka Sources</h2>
              <p className="mt-2 text-sm text-muted-foreground">Identifying department, retrieving probable documents, and preparing your step-by-step procedure.</p>
            </div>
          )}

          {result && (
            <article className="rounded-2xl border bg-card p-5 sm:p-8 shadow-sm">
              {/* Service Guide Header */}
              <div className="flex flex-wrap items-start justify-between gap-4 border-b pb-6">
                <div className="flex items-start gap-3">
                  <span className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
                    <Building2 className="h-6 w-6" />
                  </span>
                  <div>
                    {identifiedDepartment && (
                      <p className="text-xs font-bold uppercase tracking-wider text-primary">{identifiedDepartment}</p>
                    )}
                    <h2 className="mt-1 text-2xl font-bold sm:text-3xl">{identifiedServiceName || "Service Guidance"}</h2>
                    {result.service_id && result.service_id !== "unknown" && (
                      <p className="mt-1 text-xs text-muted-foreground">Service Code: {result.service_id}</p>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  {/* Verification Status Badge */}
                  {infoStatus === "Officially verified" && (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-800">
                      <ShieldCheck className="h-4 w-4 text-emerald-600" /> Officially verified
                    </span>
                  )}
                  {infoStatus === "Probable" && (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-800">
                      <AlertTriangle className="h-4 w-4 text-amber-600" /> Probable (Research lead)
                    </span>
                  )}
                  {infoStatus === "Not found" && (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-300 bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
                      Not found
                    </span>
                  )}
                  <button onClick={() => setResult(null)} aria-label="Close guide" className="rounded-lg border p-2 hover:bg-muted">
                    <X className="h-4 w-4" />
                  </button>
                </div>
              </div>

              {/* Clarification Needed Flow */}
              {result.status === "clarification_needed" && (
                <div className="mt-6 rounded-xl border border-amber-300 bg-amber-50/50 p-6">
                  <h3 className="font-bold text-lg text-amber-950 flex items-center gap-2">
                    <AlertTriangle className="h-5 w-5 text-amber-600" /> Clarification needed
                  </h3>
                  <p className="mt-2 text-sm text-amber-900">{result.clarification_question || result.question || result.message}</p>
                  <form className="mt-4 flex flex-col gap-3 sm:flex-row" onSubmit={(e) => { e.preventDefault(); void submitRequest(); }}>
                    <input
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="Type your clarification reply here..."
                      className="min-w-0 flex-1 rounded-xl border bg-background px-4 py-3 text-sm"
                    />
                    <button disabled={loading || !query.trim()} className="rounded-xl bg-primary px-6 py-3 font-semibold text-primary-foreground disabled:opacity-50">
                      Continue
                    </button>
                  </form>
                </div>
              )}

              {/* Unsupported Message (if no research is attached) */}
              {result.status === "unsupported" && !result.research && (
                <div className="mt-6 rounded-xl border p-5">
                  <h3 className="font-bold text-lg">We need more information</h3>
                  <p className="mt-2 text-sm">{result.message || "This request could not be matched to an initial supported service."}</p>
                  {result.suggested_services && result.suggested_services.length > 0 && (
                    <div className="mt-4">
                      <p className="text-xs font-semibold text-muted-foreground uppercase">Supported Services you can try:</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {result.suggested_services.map((s) => (
                          <button
                            key={s}
                            type="button"
                            onClick={() => {
                              const promptMap: Record<string, string> = {
                                aadhaar_update: "I want to update my Aadhaar address. What documents do I need?",
                                income_certificate: "How can I apply for an income certificate in Karnataka?",
                                civic_complaint: "How do I file a civic complaint?",
                              };
                              setQuery(promptMap[s] || s);
                            }}
                            className="rounded-lg border bg-muted/50 px-3 py-1.5 text-xs font-medium hover:bg-muted"
                          >
                            {s.replace(/_/g, " ")}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {result.official_portal_url && (
                    <a className="mt-4 inline-flex items-center gap-2 text-primary text-sm font-semibold underline" href={result.official_portal_url} target="_blank" rel="noreferrer">
                      Open general portal <ArrowRight className="h-4 w-4" />
                    </a>
                  )}
                </div>
              )}

              {/* Main Guide Content (When Ok or Research is available) */}
              {hasGuideContent && (
                <>
                  {/* Key Highlights Grid */}
                  <div className="mt-6 grid gap-4 sm:grid-cols-3">
                    {/* Online Portal Card */}
                    <div className="rounded-xl border bg-muted/20 p-4">
                      <span className="text-xs font-semibold uppercase text-muted-foreground">Online Option</span>
                      <p className="mt-1 font-semibold text-sm">
                        {onlineUrl ? "Available Online" : "Check Official Channel"}
                      </p>
                      {onlineUrl && (
                        <a
                          href={onlineUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-2 inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
                        >
                          Open Application Portal <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      )}
                    </div>

                    {/* Offline Centre / Office Card */}
                    <div className="rounded-xl border bg-muted/20 p-4">
                      <span className="text-xs font-semibold uppercase text-muted-foreground">Offline Authority</span>
                      <p className="mt-1 font-semibold text-sm">{offlineOption || "District / Taluk Citizen Centres"}</p>
                      <p className="mt-1 text-xs text-muted-foreground">In-person application available</p>
                    </div>

                    {/* Fee Information Card */}
                    <div className="rounded-xl border bg-muted/20 p-4">
                      <span className="text-xs font-semibold uppercase text-muted-foreground">Statutory Fee</span>
                      <p className="mt-1 font-semibold text-sm">
                        {result.fee?.amount != null ? `${result.fee.currency || "INR"} ${result.fee.amount}` : "Free / As per department rules"}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">{result.fee?.note || "Official government fee"}</p>
                    </div>
                  </div>

                  {/* Document Checklist Section */}
                  <section className="mt-8">
                    <h3 className="flex items-center gap-2 text-lg font-bold">
                      <FileText className="h-5 w-5 text-primary" /> Probable Required Documents
                    </h3>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Documents identified through official government guidelines and portal research:
                    </p>
                    <div className="mt-4 grid gap-2.5 sm:grid-cols-2">
                      {displayedDocs.map((doc, idx) => {
                        const name = doc.name;
                        const note = doc.note;
                        const status = doc.status;
                        return (
                          <div key={idx} className="flex items-start gap-3 rounded-xl border bg-card p-3.5 shadow-xs">
                            <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-emerald-100 text-emerald-700">
                              <Check className="h-3.5 w-3.5" />
                            </span>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center justify-between gap-2">
                                <p className="font-semibold text-sm leading-snug">{name}</p>
                                <span className={`shrink-0 rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${
                                  status === "conditional" ? "bg-amber-100 text-amber-800" : "bg-emerald-100 text-emerald-800"
                                }`}>
                                  {status}
                                </span>
                              </div>
                              {note && <p className="mt-1 text-xs text-muted-foreground leading-relaxed">{note}</p>}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </section>

                  {/* Step-by-Step Application Procedure */}
                  <section className="mt-8">
                    <h3 className="flex items-center gap-2 text-lg font-bold">
                      <CheckCircle2 className="h-5 w-5 text-primary" /> Step-by-Step Application Procedure
                    </h3>
                    <ol className="mt-4 space-y-3">
                      {displayedSteps.map((step, idx) => (
                        <li key={idx} className="flex gap-3.5 rounded-xl border bg-card p-4 shadow-xs">
                          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-primary/10 font-bold text-sm text-primary">
                            {idx + 1}
                          </span>
                          <p className="pt-0.5 text-sm leading-relaxed">{step}</p>
                        </li>
                      ))}
                    </ol>
                  </section>

                  {/* Official Sources Section */}
                  {officialSources.length > 0 && (
                    <section className="mt-8 rounded-xl border bg-muted/10 p-5">
                      <h3 className="flex items-center gap-2 text-base font-bold">
                        <Globe className="h-5 w-5 text-primary" /> Authoritative Official Sources
                      </h3>
                      <p className="mt-1 text-xs text-muted-foreground">Information is cross-referenced with designated government portals:</p>
                      <ul className="mt-3 divide-y">
                        {officialSources.map((src, i) => (
                          <li key={i} className="py-2.5 first:pt-0 last:pb-0 flex items-center justify-between gap-2">
                            <span className="font-medium text-sm">{src.title}</span>
                            {src.url && (
                              <a
                                href={src.url}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline shrink-0"
                              >
                                Visit Source <ExternalLink className="h-3 w-3" />
                              </a>
                            )}
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}

                  {/* Disclaimers & Notes */}
                  {result.warnings && result.warnings.length > 0 && (
                    <div className="mt-6 rounded-xl border border-amber-300 bg-amber-50/60 p-4 text-xs text-amber-950">
                      <h4 className="font-bold flex items-center gap-1.5"><AlertTriangle className="h-4 w-4 text-amber-700" /> Important Notes</h4>
                      <ul className="mt-1.5 list-disc pl-5 space-y-1">
                        {result.warnings.map((w, i) => <li key={i}>{w}</li>)}
                      </ul>
                    </div>
                  )}

                  {/* ======================================================== */}
                  {/* STEP 1 TO STEP 6: NEARBY SERVICE CENTRE DISCOVERY FLOW */}
                  {/* ======================================================== */}
                  <section ref={discoveryRef} className="mt-10 rounded-2xl border-2 border-primary/40 bg-gradient-to-b from-primary/5 to-transparent p-6 shadow-md">
                    <div className="flex flex-wrap items-center justify-between gap-4">
                      <div>
                        <span className="inline-flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-primary">
                          <Compass className="h-4 w-4" /> Service Centre Discovery
                        </span>
                        <h3 className="mt-1 text-xl sm:text-2xl font-bold">Would you like to find a nearby service centre?</h3>
                        <p className="mt-1 text-sm text-muted-foreground">
                          Locate candidate centres in Karnataka to submit applications or complete in-person verification.
                        </p>
                      </div>

                      {!discoveryActive && (
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={handleUseBrowserLocation}
                            className="inline-flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 font-semibold text-sm text-primary-foreground shadow hover:bg-primary/90"
                          >
                            <MapPin className="h-4 w-4" /> Find Nearby Centres (GPS)
                          </button>
                          <button
                            type="button"
                            onClick={() => { setDiscoveryActive(true); void discoverCentres(null, localityText); }}
                            className="inline-flex items-center gap-2 rounded-xl border bg-background px-4 py-2.5 font-semibold text-sm hover:bg-muted"
                          >
                            Search by Area / Taluk
                          </button>
                        </div>
                      )}
                    </div>

                    {/* Discovery Search Bar & Controls */}
                    {discoveryActive && (
                      <div className="mt-6 space-y-4">
                        <div className="flex flex-col gap-3 sm:flex-row">
                          <div className="relative flex-1">
                            <MapPin className="absolute left-3.5 top-3 h-4 w-4 text-muted-foreground" />
                            <input
                              value={localityText}
                              onChange={(e) => setLocalityText(e.target.value)}
                              placeholder="Enter district, taluk or locality (e.g. Koramangala, Indiranagar, Mysuru)..."
                              className="w-full rounded-xl border bg-background pl-10 pr-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-primary"
                            />
                          </div>
                          <button
                            type="button"
                            onClick={() => void discoverCentres(userCoords, localityText)}
                            disabled={centresLoading}
                            className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-5 py-2.5 font-semibold text-sm text-primary-foreground shadow hover:bg-primary/90 disabled:opacity-50"
                          >
                            {centresLoading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                            Search Centres
                          </button>
                          <button
                            type="button"
                            onClick={handleUseBrowserLocation}
                            disabled={centresLoading}
                            className="inline-flex items-center justify-center gap-2 rounded-xl border bg-background px-4 py-2.5 font-medium text-sm hover:bg-muted disabled:opacity-50"
                          >
                            <Navigation className="h-4 w-4 text-primary" /> Use My Location
                          </button>
                        </div>

                        {userCoords && (
                          <p className="text-xs text-emerald-700 flex items-center gap-1 font-medium">
                            <CheckCircle2 className="h-3.5 w-3.5" /> Coordinates acquired ({userCoords.latitude.toFixed(4)}, {userCoords.longitude.toFixed(4)}). Distance calculated via GPS.
                          </p>
                        )}

                        {centresError && (
                          <div className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
                            {centresError}
                          </div>
                        )}

                        {/* Interactive Google Map Toggle */}
                        <div className="flex items-center justify-between border-t pt-4">
                          <p className="text-xs font-semibold uppercase text-muted-foreground">
                            {candidateCentres.length} Candidate Centre{candidateCentres.length === 1 ? "" : "s"} Discovered
                          </p>
                          <button
                            type="button"
                            onClick={() => setShowInteractiveMap(!showInteractiveMap)}
                            className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
                          >
                            <MapPin className="h-3.5 w-3.5" /> {showInteractiveMap ? "Hide Interactive Map" : "View on Google Map"}
                          </button>
                        </div>

                        {showInteractiveMap && (
                          <div className="overflow-hidden rounded-xl border">
                            <NearbyCentresMap
                              searchArea={localityText.trim()}
                              serviceId={getResolvedServiceId()}
                            />
                          </div>
                        )}

                        {/* Candidate Centres Cards Grid */}
                        <div className="mt-4 grid gap-3 sm:grid-cols-2">
                          {candidateCentres.map((centre) => {
                            const isSelected = selectedCentre?.id === centre.id;
                            return (
                              <article
                                key={centre.id || centre.name}
                                className={`rounded-xl border p-4 text-left transition-all ${
                                  isSelected
                                    ? "border-primary bg-primary/10 shadow-md ring-2 ring-primary"
                                    : "bg-card hover:border-primary/50 shadow-xs"
                                }`}
                              >
                                <div className="flex items-start justify-between gap-2">
                                  <h4 className="font-bold text-sm leading-snug">{centre.name}</h4>
                                  <span className="shrink-0 rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-800">
                                    Candidate centre
                                  </span>
                                </div>
                                {centre.centre_type && (
                                  <p className="mt-1 text-xs font-medium text-primary">{centre.centre_type}</p>
                                )}
                                <p className="mt-1.5 text-xs text-muted-foreground line-clamp-2">{centre.address}</p>

                                <div className="mt-3 flex items-center justify-between gap-2 border-t pt-2.5">
                                  <span className="text-xs font-bold text-emerald-700">
                                    {centre.distance_km != null
                                      ? `${centre.distance_km.toFixed(1)} km away`
                                      : `${centre.taluk || centre.district || "Karnataka"}`}
                                  </span>
                                  <button
                                    type="button"
                                    onClick={() => handleSelectCentre(centre)}
                                    className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${
                                      isSelected
                                        ? "bg-primary text-primary-foreground shadow-xs"
                                        : "border bg-background hover:bg-muted"
                                    }`}
                                  >
                                    <MapPin className="h-3.5 w-3.5" />
                                    {isSelected ? "Selected" : "Select Centre"}
                                  </button>
                                </div>
                              </article>
                            );
                          })}
                        </div>

                        {/* STEP 6: GOOGLE MAPS NAVIGATION PROMPT */}
                        {showDirectionsModal && selectedCentre && (
                          <div className="mt-6 rounded-2xl border-2 border-primary bg-background p-6 shadow-xl animate-in fade-in duration-200">
                            <div className="flex items-start justify-between gap-4">
                              <div className="flex items-start gap-3">
                                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground">
                                  <Navigation className="h-5 w-5" />
                                </span>
                                <div>
                                  <span className="text-xs font-bold uppercase tracking-wider text-primary">Directions Ready</span>
                                  <h4 className="mt-1 text-lg font-bold">
                                    Would you like directions to this service centre?
                                  </h4>
                                  <p className="mt-1 text-sm text-foreground font-semibold">
                                    {selectedCentre.name}
                                  </p>
                                  <p className="text-xs text-muted-foreground">
                                    {selectedCentre.address}
                                  </p>
                                  {selectedCentre.distance_km != null && (
                                    <p className="mt-1 text-xs font-bold text-emerald-700">
                                      Approx. {selectedCentre.distance_km.toFixed(1)} km from your current location
                                    </p>
                                  )}
                                </div>
                              </div>
                              <button
                                type="button"
                                onClick={() => setShowDirectionsModal(false)}
                                className="rounded-lg border p-1.5 hover:bg-muted"
                              >
                                <X className="h-4 w-4" />
                              </button>
                            </div>

                            <div className="mt-6 flex flex-wrap items-center gap-3">
                              <button
                                type="button"
                                onClick={handleOpenGoogleMaps}
                                className="inline-flex items-center gap-2 rounded-xl bg-primary px-6 py-3 font-semibold text-sm text-primary-foreground shadow hover:bg-primary/90 transition-colors"
                              >
                                <Navigation className="h-4 w-4" /> YES – OPEN GOOGLE MAPS
                              </button>
                              <button
                                type="button"
                                onClick={() => setShowDirectionsModal(false)}
                                className="inline-flex items-center gap-2 rounded-xl border bg-background px-5 py-3 font-semibold text-sm hover:bg-muted transition-colors"
                              >
                                NO – RETURN TO CIVICROUTE
                              </button>
                            </div>
                            <p className="mt-3 text-[11px] text-muted-foreground">
                              Directions will open in the Google Maps app or browser from your location to the centre.
                            </p>
                          </div>
                        )}
                      </div>
                    )}
                  </section>
                </>
              )}
            </article>
          )}
        </section>

        {/* About Section */}
        <section id="about" className="border-t bg-muted/30">
          <div className="mx-auto max-w-5xl px-4 py-12">
            <h2 className="text-2xl font-bold">About CivicRoute</h2>
            <p className="mt-3 max-w-3xl leading-7 text-muted-foreground">
              CivicRoute is an AI-powered public service navigator built for Karnataka citizens. It removes the confusion of dealing with administrative bureaucracy by explaining exactly which department handles your request, what documents you need, how to apply step-by-step, and where to find the nearest service centre.
            </p>
            <div className="mt-6 flex flex-wrap gap-4 text-xs text-muted-foreground">
              <span>Authoritative Source Verification</span>
              <span>•</span>
              <span>Karnataka Sakala Guarantee</span>
              <span>•</span>
              <span>UIDAI Integration</span>
              <span>•</span>
              <span>Google Maps Navigation</span>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t px-4 py-6 text-center text-sm text-muted-foreground">
        CivicRoute · Karnataka Public Service Navigator · Hackathon MVP 2026
      </footer>
    </div>
  );
}
