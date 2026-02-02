# Business Finder - Complete Implementation Plan

## Executive Summary

Build a full-featured web application to search for US businesses using prompt-based criteria, with iterative geographic searching, evidence-based data enrichment, and Google Sheets export.

**Implementation Approach**: Use existing HTML/Tailwind CSS mockups in `/frontend/` folder as source of truth for React component conversion

---

## Table of Contents

1. [Product Overview](#product-overview)
2. [Core Concepts](#core-concepts)
3. [Technology Stack](#technology-stack)
4. [Frontend Implementation](#frontend-implementation)
5. [Backend Architecture](#backend-architecture)
6. [Database Schema](#database-schema)
7. [API Integration Strategy](#api-integration-strategy)
8. [Geographic Search Algorithm](#geographic-search-algorithm)
9. [Data Enrichment Pipeline](#data-enrichment-pipeline)
10. [Google Sheets Export](#google-sheets-export)
11. [Prompt Version Control](#prompt-version-control)
12. [Operational Guardrails](#operational-guardrails)
13. [Implementation Phases](#implementation-phases)
14. [Cost Estimates](#cost-estimates)
15. [Open Decisions](#open-decisions)
16. [Verification Plan](#verification-plan)
17. [Documentation](#documentation)

---

## Product Overview

### Goal

Build a US-only web app that discovers businesses in a user-defined area, then enriches and filters them based on prompt-driven criteria (employees, years in business, and—when available—owner name), and exports results to Google Sheets.

### Target User

- **Personal use** (single user or small team)
- **Geographic Scope**: US businesses only
- **Platform Type**: Full-featured web app (not MVP)
- **API Strategy**: Free tier + paid tier options + BYO API keys

---

## Core Concepts

- **Project**: A saved configuration (area, criteria prompt + versions, source settings, export settings)
- **Run**: An execution of discovery + enrichment under a specific pinned prompt version and budget
- **Business (canonical record)**: A deduped entity with stable IDs, normalized address/phone, and provenance
- **Evidence-first enrichment**: Every enriched field includes source + timestamp + confidence + matching score

---

## Technology Stack

### Frontend

**Framework & Build**:
- React 18 with TypeScript
- Vite (build tool)

**UI Components**:
- shadcn/ui (Radix UI primitives) - use MCP server for implementation
- Tailwind CSS (styling)

**State Management**:
- Zustand (lightweight, simple state management)

**Forms**:
- React Hook Form (performant form handling)
- Zod (type-safe validation)

**Real-time Updates**:
- WebSocket (native) for live progress

**Charts**:
- Recharts (simple, composable charts)

**HTTP Client**:
- Axios

**Routing**:
- React Router DOM v6

**Source**: Convert HTML mockups from `/frontend/` folder to React components

### Backend

- FastAPI (Python 3.11+) - Async API with automatic OpenAPI docs
- Celery + Redis - Task queue for long-running searches
- PostgreSQL 15+ - Relational database
- SQLAlchemy 2.0 async ORM - Database ORM
- JWT tokens - Authentication

### Infrastructure

- Railway - Backend + database hosting
- Vercel - Frontend hosting
- Redis - Cache and task queue
- Sentry (free tier) - Error tracking

---

## Frontend Implementation

### Source Files: `/frontend/` Folder

The frontend folder contains **25 complete HTML mockups** with Tailwind CSS styling and PNG screenshots:

1. **login_and_authentication_page/** - Login screen with email/password + Google OAuth
2. **business_finder_dashboard/** - Main dashboard with stats and recent runs
3. **projects_management_page/** - Project list with grid/list views
4. **create_project_wizard_-_step_1/** - Project name and description
5. **project_wizard_-_area_definition/** - Geographic area selection
6. **project_wizard_-_data_sources/** - Discovery and enrichment source configuration
7. **new_run_configuration_page/** - Create new run from project
8. **run_monitor_page/** - Real-time run progress monitoring
9. **search_results_explorer/** - Results table with filters and sorting
10. **business_details_-_overview_tab/** - Business basic information
11. **business_details_-_enrichments_tab/** - Enrichment fields with confidence scores
12. **business_details_-_evidence_tab/** - Evidence sources with excerpts
13. **business_details_-_raw_data_tab/** - JSON payload viewer
14. **export_manager_-_step_1:_mode/** - Export mode selection
15. **export_manager_-_step_2:_destination/** - Spreadsheet destination selection
16. **export_manager_-_step_3:_config/** - Column mapping and format options
17. **export_progress_modal/** - Export progress tracking
18. **export_success_modal/** - Completion with spreadsheet link
19. **business_review_queue/** - Review "needs_review" items
20. **edit_business_values_modal/** - Manual value override
21. **user_settings_profile/** - User profile settings
22. **settings_-_notification_preferences/** - Email and in-app notifications
23. **settings_-_billing_and_usage/** - Usage stats and payment info
24. **settings_-_api_keys_management/** - BYO API key management
25. **add/** - Additional components

### Conversion Process: HTML → React Components

**Step 1: Analyze HTML Files**
- Read each `code.html` file in `/frontend/` subfolder
- Extract component structure, Tailwind classes, layout
- Reference `screen.png` for visual confirmation

**Step 2: Create React Component**
- Convert HTML structure to JSX (React-compatible)
- Replace HTML classes with shadcn/ui components where applicable
- Example:
  - HTML: `<button class="bg-primary text-white px-4 py-2 rounded">`
  - React: `<Button className="bg-primary text-white px-4 py-2">`

**Step 3: Add TypeScript Types**
- Define props interfaces based on HTML element attributes
- Example:
  ```typescript
  interface BusinessCardProps {
    name: string;
    address: string;
    matchScore: number;
    onViewDetails: () => void;
  }
  ```

**Step 4: Integrate State Management**
- Connect to Zustand stores (authStore, projectStore, runStore)
- Replace static data with dynamic data from API calls
- Add loading states and error handling

**Step 5: Add Interactivity**
- Implement onClick handlers
- Add form validation (React Hook Form + Zod)
- Connect WebSocket for real-time updates

### Component Mapping

**Layout Components** (from HTML):
- Header/Navigation → `src/components/layout/Header.tsx`
- Sidebar → `src/components/layout/Sidebar.tsx`
- Footer → `src/components/layout/Footer.tsx`

**Page Components** (from HTML):
- Login → `src/pages/LoginPage.tsx`
- Dashboard → `src/pages/DashboardPage.tsx`
- Projects → `src/pages/ProjectsPage.tsx`
- New Run → `src/pages/NewRunPage.tsx`
- Run Monitor → `src/pages/RunMonitorPage.tsx`
- Results → `src/pages/ResultsPage.tsx`
- Review Queue → `src/pages/ReviewQueuePage.tsx`
- Settings → `src/pages/SettingsPage.tsx`

**Feature Components** (from HTML):
- Stat Cards → `src/components/dashboard/StatCard.tsx`
- Project Cards → `src/components/projects/ProjectCard.tsx`
- Results Table → `src/components/results/ResultsTable.tsx`
- Business Details Modal → `src/components/business/BusinessDetailsModal.tsx`
- Export Manager → `src/components/exports/ExportManagerModal.tsx`

### shadcn/ui Component Integration

Use shadcn/ui CLI to install components that match HTML elements:

```bash
# Install shadcn/ui
npx shadcn-ui@latest init

# Add components (matching HTML mockups)
npx shadcn-ui@latest add button
npx shadcn-ui@latest add input
npx shadcn-ui@latest add card
npx shadcn-ui@latest add dialog
npx shadcn-ui@latest add table
npx shadcn-ui@latest add tabs
npx shadcn-ui@latest add badge
npx shadcn-ui@latest add progress
npx shadcn-ui@latest add form
npx shadcn-ui@latest add select
npx shadcn-ui@latest add checkbox
npx shadcn-ui@latest add radio-group
npx shadcn-ui@latest add switch
npx shadcn-ui@latest add slider
npx shadcn-ui@latest add separator
npx shadcn-ui@latest add scroll-area
npx shadcn-ui@latest add dropdown-menu
```

**IMPORTANT**: Always use the shadcn/ui MCP server when implementing components. Call the demo tool first to understand component usage.

### Styling Consistency

**From HTML Mockups**:
- **Primary Color**: `#136dec` (blue)
- **Background Light**: `#f6f7f8`
- **Background Dark**: `#101822`
- **Border Radius**: `0.25rem` (default), `0.5rem` (lg), `0.75rem` (xl)
- **Font**: Inter
- **Icons**: Material Symbols Outlined

**Implementation**:
```typescript
// tailwind.config.js
export default {
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: '#136dec',
        'background-light': '#f6f7f8',
        'background-dark': '#101822',
      },
      fontFamily: {
        display: ['Inter', 'sans-serif'],
      },
      borderRadius: {
        DEFAULT: '0.25rem',
        lg: '0.5rem',
        xl: '0.75rem',
        full: '9999px',
      },
    },
  },
}
```

---

## Backend Architecture

### Geographic Search Algorithm

**Hexagonal Grid Generation**:
```python
def generate_search_grid(location, radius_miles, grid_spacing=0.7):
    """
    Generate hexagonal grid points for comprehensive coverage.
    Hexagonal packing provides ~91% coverage efficiency.
    """
    # Step 1: Geocode location to center coordinates
    center = geocode_with_locationiq(location)

    # Step 2: Calculate grid dimensions
    rows = int(radius_miles * 2 / grid_spacing)

    # Step 3: Generate hexagonal grid
    points = []
    for row in range(-rows, rows + 1):
        offset = (grid_spacing / 2) if row % 2 != 0 else 0
        cols = int(radius_miles * 2 / grid_spacing)

        for col in range(-cols, cols + 1):
            lat = center['lat'] + (row * grid_spacing / 69)
            lng = center['lng'] + ((col * grid_spacing + offset) / (69 * cos(radians(center['lat'])))

            if haversine_distance(center, (lat, lng)) <= radius_miles:
                points.append({'lat': lat, 'lng': lng})

    return points
```

**Search Execution Flow**:
1. Generate grid points (e.g., 145 points for 200 sq miles at 0.7-mile spacing)
2. For each point:
   - Call Google Places Nearby Search API with 1-mile radius
   - Extract business name, address, phone, website, place_id
   - Store raw results in database
3. Deduplicate across all grid points by name+address+phone
4. For unique businesses:
   - If paid tier: Call Proxycurl API for employee count, founded year
   - Calculate match score against user criteria
   - Filter businesses below threshold
5. Return sorted results by match score

---

## Database Schema

### Core Tables

**Users & Authentication**:
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    tier VARCHAR(50) DEFAULT 'free', -- free, paid
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE user_api_keys (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(100) NOT NULL, -- proxycurl, clearbit, google_places
    key_name VARCHAR(255),
    api_key_encrypted TEXT NOT NULL, -- encrypted at rest
    is_active BOOLEAN DEFAULT TRUE,
    precedence INTEGER DEFAULT 0, -- higher = preferred
    created_at TIMESTAMP DEFAULT NOW(),
    last_used_at TIMESTAMP,
    usage_count INTEGER DEFAULT 0
);
```

**Projects & Prompts**:
```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    area_definition JSONB NOT NULL,
    source_settings JSONB,
    export_settings JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE prompt_versions (
    id UUID PRIMARY KEY,
    project_id UUID REFERENCES projects(id),
    version INTEGER NOT NULL,
    criteria JSONB NOT NULL,
    content TEXT,
    pinned BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    created_by UUID REFERENCES users(id),
    CONSTRAINT unique_project_version UNIQUE (project_id, version)
);
```

**Runs & Businesses**:
```sql
CREATE TABLE runs (
    id UUID PRIMARY KEY,
    project_id UUID REFERENCES projects(id),
    prompt_version_id UUID REFERENCES prompt_versions(id),
    status VARCHAR(50) DEFAULT 'pending',
    budgets JSONB,
    actual_usage JSONB,
    total_found INTEGER DEFAULT 0,
    total_enriched INTEGER DEFAULT 0,
    total_matching INTEGER DEFAULT 0,
    progress_percentage INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE businesses (
    id UUID PRIMARY KEY,
    name VARCHAR(500) NOT NULL,
    address_normalized TEXT,
    city VARCHAR(255),
    state VARCHAR(100),
    zip_code VARCHAR(20),
    phone_normalized VARCHAR(50),
    website VARCHAR(500),
    industry VARCHAR(255),
    latitude DECIMAL(10, 8),
    longitude DECIMAL(11, 8),
    first_seen_at TIMESTAMP DEFAULT NOW(),
    last_seen_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT unique_canonical_business UNIQUE (
        normalize_name(name),
        normalize_address(address_normalized),
        normalize_phone(phone_normalized)
    )
);

CREATE TABLE business_sources (
    id UUID PRIMARY KEY,
    business_id UUID REFERENCES businesses(id) ON DELETE CASCADE,
    run_id UUID REFERENCES runs(id),
    source_provider VARCHAR(100) NOT NULL,
    source_id VARCHAR(500),
    source_url TEXT,
    raw_payload_hash VARCHAR(64),
    discovered_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT unique_business_source UNIQUE (business_id, source_provider, source_id)
);
```

**Enrichments & Evidence**:
```sql
CREATE TABLE enrichments (
    id UUID PRIMARY KEY,
    business_id UUID REFERENCES businesses(id) ON DELETE CASCADE,
    run_id UUID REFERENCES runs(id),
    field_name VARCHAR(100) NOT NULL,
    value_type VARCHAR(50) NOT NULL,
    value_min INTEGER,
    value_max INTEGER,
    value_exact INTEGER,
    value_text VARCHAR(500),
    confidence DECIMAL(5, 4),
    match_score INTEGER,
    observed_at TIMESTAMP DEFAULT NOW(),
    enriched_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT unique_business_field_run UNIQUE (business_id, field_name, run_id)
);

CREATE TABLE evidence (
    id UUID PRIMARY KEY,
    enrichment_id UUID REFERENCES enrichments(id) ON DELETE CASCADE,
    source_type VARCHAR(100) NOT NULL,
    source_url TEXT,
    source_id VARCHAR(500),
    excerpt TEXT,
    excerpt_hash VARCHAR(64),
    raw_html_ref TEXT,
    fetched_at TIMESTAMP DEFAULT NOW(),
    retention_until TIMESTAMP,
    is_allowlisted BOOLEAN DEFAULT FALSE,
    credibility_score DECIMAL(5, 4)
);
```

**Exports & Audit**:
```sql
CREATE TABLE exports (
    id UUID PRIMARY KEY,
    run_id UUID REFERENCES runs(id),
    user_id UUID REFERENCES users(id),
    export_mode VARCHAR(50) NOT NULL,
    destination_type VARCHAR(50) NOT NULL,
    spreadsheet_id VARCHAR(255),
    spreadsheet_url TEXT,
    tab_name VARCHAR(255),
    column_mapping JSONB,
    status VARCHAR(50) DEFAULT 'pending',
    total_rows INTEGER,
    exported_rows INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE TABLE prompt_audit_log (
    id UUID PRIMARY KEY,
    prompt_id UUID REFERENCES prompt_versions(id),
    user_id UUID REFERENCES users(id),
    action VARCHAR(50) NOT NULL,
    old_version INTEGER,
    new_version INTEGER,
    changes JSONB,
    timestamp TIMESTAMP DEFAULT NOW()
);

CREATE TABLE run_audit_log (
    id UUID PRIMARY KEY,
    run_id UUID REFERENCES runs(id),
    user_id UUID REFERENCES users(id),
    event_type VARCHAR(100) NOT NULL,
    details JSONB,
    timestamp TIMESTAMP DEFAULT NOW()
);
```

---

## API Integration Strategy

### Geographic APIs (US Only)

- **LocationIQ**: 5,000 free requests/month for geocoding
- **Google Maps Geocoding**: $5 per 1,000 requests (backup)

### Business Discovery APIs

- **Google Places API**: $0.50/request, $200 free credit/month
  - Near Search, Text Search, Place Details
  - Fields: name, address, phone, website, types, place_id
- **Yelp Fusion API**: Free tier limited, then $229/month
  - Business Search, Business Details
  - Categories: restaurants, shopping, local services
- **Apify Google Maps Scraper**: $49/month for 100,000 results
  - Fallback when both APIs quota exhausted

### Data Enrichment APIs

- **Free Tier**: Business website pages only (no SERP scraping)
- **Paid Tier - Proxycurl**: $0.009/request for employee count, founded year
- **Backup - Clearbit**: $99/month for 5,000 requests

### Google Sheets API

- **Authentication**: OAuth2 user flow
- **Library**: gspread (Python) with automatic retry
- **Operations**: Create spreadsheet, add worksheet, append rows, batch update
- **Rate Limit**: 300 requests per 60 seconds with exponential backoff

---

## Data Enrichment Pipeline

### Enrichment Fields

- `employee_count` (range preferred; exact when credible)
- `established_year` (and derive `years_in_business`)
- `owner_name` (explicitly stated only; no guessing)
- `trademark_signal` (optional; stored separately; not treated as business formation)

### Free Tier (Bounded, Compliant)

- **Source restriction**: Business website pages only (no SERP scraping)
- **Allowlist**: Only `.gov` domains and pre-approved sources
- **Fields extracted**:
  - Employee count (from "About Us", "Team" pages)
  - Founded year (from "About", "History" pages)
  - Owner name (only when explicitly stated on website)
- **Output**: `unknown` or `needs_review` when data is ambiguous
- **Evidence tracking**: All data includes source URL, confidence score, timestamp

### Paid Tier

- **Proxycurl**: $0.009/request for employee count, founded year
- **Clearbit**: $99/month for 5,000 requests (backup)
- **Evidence tracking**: All data includes source API, request ID, timestamp, confidence
- **Cascading fallback**: Proxycurl → Clearbit → Skip with warning

---

## Google Sheets Export

### Export Modes

1. **Append to one long tab**: Add all results to a single tab. Good for cumulative exports.
2. **Separate tabs per run**: Create a new tab for each run. Good for comparing runs.
3. **Separate tabs per zip code**: Group results by zip code. Good for geographic analysis.
4. **New spreadsheet per run**: Create a new spreadsheet for each run. Good for sharing.

### Features

- Idempotent upserts (avoid duplicates)
- Column template mapping
- Export audit log
- Batch writing (100 rows per request)
- Rate limit handling

---

## Prompt Version Control

- **Immutable History**: All versions preserved with timestamps
- **Pinned Version**: Active version for runs
- **Restore**: Rollback to any previous version
- **Diff**: Compare versions side-by-side
- **Soft Delete Protection**: Can't accidentally delete active version

---

## Operational Guardrails

### Per-Run Budgets

- **Request limits**: Max requests per run (e.g., 10,000)
- **Time limits**: Max execution time per run (e.g., 60 minutes)
- **Cost limits**: Max cost per run in USD (e.g., $100)
- **Automatic termination**: Stop run when any budget exceeded
- **User notification**: Alert user with reason for termination

### Per-Business Budgets

- **Max enrichment attempts**: Limit API calls per business (e.g., 3)
- **Max fetch size**: Limit bytes downloaded per website (e.g., 5MB)
- **Max timeout**: Timeout per business enrichment (e.g., 30 seconds)
- **Skip on failure**: Mark as `needs_review` after budget exhausted

### Rate Limiting & Circuit Breakers

- **Per-domain rate limiting**: Respect each API's rate limits
- **Exponential backoff**: Retry with increasing delays (1s, 2s, 4s, 8s, 16s)
- **Circuit breakers**: Temporarily disable failing APIs after N consecutive failures
- **Redis caching**:
  - Geocoding results: 24h TTL
  - Business lookups: 7d TTL
  - Enrichments: 30d TTL

### Evidence Storage Policy

- **URLs + excerpts**: Store indefinitely for audit trail
- **Raw HTML**: Temporary storage only (7-30 days, then delete)
- **Hash-based deduplication**: Don't store duplicate excerpts from same source
- **S3/Backblaze**: Store large raw data outside database
- **Retention jobs**: Scheduled cleanup of expired raw data

---

## Implementation Phases

### Phase 0: Frontend Analysis & Setup (Week 1)

**Tasks**:
1. **Analyze HTML Mockups**
   - Read all 25 `code.html` files in `/frontend/` folder
   - Document component structure, props, and styling patterns
   - Create component inventory

2. **Set Up React Project**
   - Initialize Vite + React + TypeScript
   - Install Tailwind CSS with matching config
   - Install shadcn/ui
   - Set up project folder structure

3. **Create Base Components**
   - Layout components (Header, Sidebar, Footer)
   - UI primitives (Button, Input, Card, Dialog, etc.)
   - Add Material Symbols Outlined icons

**Deliverables**:
- Component inventory document
- Working React + Vite project with Tailwind + shadcn/ui

**Critical Files**:
- `frontend/COMPONENT_INVENTORY.md` - List of all components to build
- `vite.config.ts` - Vite configuration
- `tailwind.config.js` - Tailwind configuration (matching HTML)
- `src/components/layout/` - Base layout components

---

### Phase 1: Project Setup & Infrastructure (Week 1-2)

**Backend Tasks**:
- Initialize FastAPI backend with PostgreSQL + Redis
- Set up React + TypeScript + Vite frontend
- Configure Celery task queue
- Implement JWT authentication system
- Design database schema and run migrations
- Set up development environment (Docker Compose)

**Frontend Tasks**:
- Convert HTML mockups for: Login, Dashboard
- Create authStore for authentication
- Implement login page with email/password + Google OAuth
- Build dashboard with stats cards and recent runs table
- Add navigation (header + sidebar)
- Connect to backend API

**Critical Files**:
- `backend/app/main.py` - FastAPI app initialization
- `backend/app/core/database.py` - PostgreSQL connection
- `backend/app/core/security.py` - JWT + OAuth authentication
- `backend/app/models/*.py` - SQLAlchemy models
- `src/pages/LoginPage.tsx` - From `login_and_authentication_page/code.html`
- `src/pages/DashboardPage.tsx` - From `business_finder_dashboard/code.html`
- `src/stores/authStore.ts` - Auth state management

---

### Phase 2: Core Search System (Week 3-4)

**Backend Tasks**:
- Implement LocationIQ geocoding integration
- Build hexagonal grid generation algorithm
- Create Google Places API search worker (Celery)
- Implement provider-based deduplication
- Build discovery orchestration API endpoints
- Create run status tracking with budget enforcement

**Frontend Tasks**:
- Convert HTML mockups for: Projects Page, Create Project Wizard (Steps 1-3)
- Build project management UI (create, view, edit, delete projects)
- Create area builder component (city/state, zip, center+radius)
- Build prompt builder component (natural language + structured)
- Implement project CRUD operations
- Add form validation (React Hook Form + Zod)

**Critical Files**:
- `backend/app/services/geographic_grid.py` - Grid generation
- `backend/app/workers/discovery_worker.py` - Celery discovery worker
- `backend/app/api/discovery.py` - Discovery API endpoints
- `backend/app/services/deduplication.py` - Provider-based deduplication
- `src/pages/ProjectsPage.tsx` - From `projects_management_page/code.html`
- `src/components/projects/CreateProjectModal.tsx` - From `create_project_wizard_-_step_1/code.html`
- `src/components/projects/AreaBuilder.tsx` - From `project_wizard_-_area_definition/code.html`
- `src/components/projects/PromptBuilder.tsx` - New component (criteria editor)

---

### Phase 3: Run Monitoring & Evidence-Based Enrichment (Week 5-7)

**Backend Tasks**:
- **Free Tier (Week 5)**:
  - Build website scraper with allowlist enforcement
  - Implement HTML parsing for employee count, founded year, owner name
  - Create evidence storage service (URLs + excerpts + raw HTML)
  - Implement confidence scoring algorithm
  - Add `unknown`/`needs_review` logic

- **Paid Tier (Week 6)**:
  - Integrate Proxycurl API for employee/founded data
  - Add Clearbit as backup
  - Implement evidence tracking for API sources
  - Create match scoring algorithm with configurable weights
  - Build enrichment worker with rate limiting + per-business budgets
  - Implement cascading fallback chain

**Frontend Tasks**:
- Convert HTML mockups for: New Run Page, Run Monitor Page
- Build run monitor with real-time WebSocket progress
- Implement progress bars, live stats cards, log viewer
- Create results preview table (first 20 results)
- Add "View All Results" navigation
- Implement WebSocket connection for live updates

**Critical Files**:
- `backend/app/workers/enrichment_worker.py` - Enrichment worker (free + paid)
- `backend/app/services/website_scraper.py` - Compliant website scraping
- `backend/app/services/proxycurl.py` - Proxycurl client
- `backend/app/services/evidence.py` - Evidence storage + retrieval
- `backend/app/services/confidence_scorer.py` - Confidence calculation
- `src/pages/NewRunPage.tsx` - From `new_run_configuration_page/code.html`
- `src/pages/RunMonitorPage.tsx` - From `run_monitor_page/code.html`
- `src/hooks/useWebSocket.ts` - WebSocket hook for real-time updates

---

### Phase 4: Filtering, Ranking & Evidence UI (Week 8-9)

**Backend Tasks**:
- Implement prompt versioning with immutable history
- Build prompt → structured filter spec compiler
- Create hard filter application service
- Implement weighted scoring with configurable weights
- Build evidence viewer backend API

**Frontend Tasks**:
- Convert HTML mockups for: Results Page, Business Details (4 tabs)
- Build results table with filters sidebar
- Implement sort, filter, pagination
- Create business details modal with tabs
- Build enrichments tab (field values with confidence)
- Build evidence tab (sources, excerpts, credibility)
- Add raw data tab (JSON viewer)
- Implement "needs review" queue

**Critical Files**:
- `backend/app/services/prompt_versioning.py` - Version control + restore
- `backend/app/services/filter_compiler.py` - Prompt → structured filters
- `src/pages/ResultsPage.tsx` - From `search_results_explorer/code.html`
- `src/components/business/BusinessDetailsModal.tsx` - From business_details HTML files
- `src/components/business/EnrichmentsTab.tsx` - From `business_details_-_enrichments_tab/code.html`
- `src/components/business/EvidenceTab.tsx` - From `business_details_-_evidence_tab/code.html`
- `src/components/business/RawDataTab.tsx` - From `business_details_-_raw_data_tab/code.html`

---

### Phase 5: Google Sheets Export (Week 10)

**Backend Tasks**:
- Set up Google OAuth2 flow
- Implement gspread client with retry logic
- Create export worker (Celery)
- Build export manager API endpoints
- Implement 4 export modes (append, per-run, per-zip, new sheet)
- Add idempotent upsert logic
- Create export audit log

**Frontend Tasks**:
- Convert HTML mockups for: Export Manager (Steps 1-3), Progress, Success
- Build export manager modal with multi-step wizard
- Implement spreadsheet selector (list user's spreadsheets)
- Create column mapping component (drag-and-drop reordering)
- Add export progress tracking
- Build export success UI with spreadsheet link

**Critical Files**:
- `backend/app/services/google_sheets.py` - Sheets API client
- `backend/app/workers/export_worker.py` - Export worker
- `backend/app/api/exports.py` - Export endpoints
- `backend/app/core/google_oauth.py` - OAuth2 flow
- `src/components/exports/ExportManagerModal.tsx` - From export_manager HTML files
- `src/components/exports/ExportProgress.tsx` - From `export_progress_modal/code.html`
- `src/components/exports/ExportSuccess.tsx` - From `export_success_modal/code.html`

---

### Phase 6: Review Queue & Settings (Week 11)

**Backend Tasks**:
- Create review queue API endpoints
- Implement manual override endpoint
- Build settings API endpoints
- Create user preferences API
- Implement BYO API key storage (encrypted)

**Frontend Tasks**:
- Convert HTML mockups for: Review Queue, Edit Values Modal, Settings (4 tabs)
- Build review queue with cards
- Implement manual override modal
- Create settings page with tabs (Profile, Notifications, Billing, API Keys)
- Build API key management UI
- Add usage charts and billing info

**Critical Files**:
- `src/pages/ReviewQueuePage.tsx` - From `business_review_queue/code.html`
- `src/components/review/EditValuesModal.tsx` - From `edit_business_values_modal/code.html`
- `src/pages/SettingsPage.tsx` - Main settings page with tabs
- `src/components/settings/ProfileTab.tsx` - From `user_settings_profile/code.html`
- `src/components/settings/NotificationsTab.tsx` - From `settings_-_notification_preferences/code.html`
- `src/components/settings/BillingTab.tsx` - From `settings_-_billing_and_usage/code.html`
- `src/components/settings/ApiKeysTab.tsx` - From `settings_-_api_keys_management/code.html`

---

### Phase 7: Testing & Hardening (Week 12-13)

**Tasks**:
- Write unit tests for all services
- Integration tests for API endpoints
- Load testing with concurrent runs
- Test budget enforcement
- Test circuit breakers
- Test evidence storage + retrieval
- Optimize database queries
- Add structured logging
- Security review

**Critical Files**:
- `backend/tests/` - Complete test suite
- `backend/app/core/errors.py` - Error handlers
- `backend/app/core/logging.py` - Structured logging
- `backend/app/monitoring/` - Metrics + health checks

---

### Phase 8: Deployment & Launch (Week 14)

**Tasks**:
- Set up Railway deployment (backend + PostgreSQL + Redis)
- Deploy frontend to Vercel
- Configure environment variables
- Set up domain + SSL
- Create user documentation
- Final end-to-end testing
- Soft launch with beta users
- Full public launch!

---

## Cost Estimates (Monthly)

### Free Tier (Development/Light Usage)

- **LocationIQ**: $0 (5,000 free requests/month)
- **Google Places**: $0 ($200 credit covers ~400 requests)
- **PostgreSQL (Railway)**: $0 (free tier)
- **Redis (Railway)**: $0 (free tier)
- **Sentry**: $0 (free tier)
- **Total**: $0/month for ~1,000 business searches

### Paid Tier (Heavy Usage)

- **Google Places**: $50-100/month (beyond free credit)
- **Proxycurl**: $50-200/month (5,000-20,000 enrichment requests)
- **PostgreSQL (Railway)**: $10-20/month
- **Redis (Railway)**: $10/month
- **Sentry**: $0 (free tier)
- **Total**: $120-330/month for ~10,000-50,000 business searches

---

## Open Decisions (Needed to Finalize MVP)

### 1. Primary Discovery Source(s)

**Question**: Which APIs should we prioritize for business discovery?

**Options**:
- **Google Places API** (best coverage, $0.50/request, $200 credit/month)
- **Yelp Fusion API** (good for restaurants/local services, $229/month minimum)
- **Apify Google Maps Scraper** ($49/month for 100K results)

**Constraints**:
- Query constraints: Some APIs only support radius search, not polygon
- Quotas: Google Places = 100 req/sec, Yelp = 5,000 req/day
- Terms: All prohibit scraping/caching beyond their terms

**Recommendation**: Google Places primary, Yelp secondary for restaurants, Apify tertiary fallback

### 2. Employee Count Output Preference

**Question**: Should we output ranges only, or allow exact numbers when credible?

**Options**:
- **Ranges only**: "51-200", "201-500" (simpler, more honest about uncertainty)
- **Exact when credible**: "250" when API provides exact, "51-200" when range (more precise but potentially misleading)
- **Both**: Store both internally, export based on user preference (most flexible)

**Recommendation**: Both internally, export user's choice (default: ranges only)

### 3. Evidence Storage Policy

**Question**: Should we store raw HTML temporarily or only URLs + excerpts?

**Options**:
- **URLs + excerpts only**: Minimal storage, faster, less privacy risk (recommended)
- **Temporary raw HTML**: 7-day retention for debugging, then delete
- **Extended retention**: 30-day retention for audit/compliance needs

**Recommendation**: URLs + excerpts indefinitely, raw HTML 7-day retention (configurable)

### 4. "Best Fit" Scoring Rubric

**Question**: How should we weight criteria when computing match scores?

**Factors to weight**:
- Employee count match (0-100 scale)
- Years in business match (0-100 scale)
- Location match (distance decay)
- Industry/category match (binary or fuzzy)

**Scoring formula options**:
- **Weighted sum**: `(employee_score * 0.4) + (years_score * 0.3) + (location_score * 0.2) + (industry_score * 0.1)`
- **Configurable weights**: User sets weights per search
- **Hard filters first**: Apply binary filters (must have X), then score remaining

**Recommendation**: Hard filters first (minimum thresholds), then weighted sum with configurable default weights

---

## Verification Plan

### End-to-End Testing

#### 1. Project & Run Management
- Create a new project with area definition (San Francisco, 50-mile radius)
- Create prompt with criteria (employee count: 50-500, established: after 2015)
- Pin prompt version v1
- Create a run referencing pinned version
- Verify run status updates: pending → running → completed
- Verify budget enforcement (stop after max requests/cost)

#### 2. Discovery & Deduplication
- Run discovery on 10-mile radius area
- Verify hexagonal grid generates ~145 search points
- Verify Google Places API called at each point
- Verify provider IDs stored in `business_sources` table
- Verify deduplication by provider IDs + normalized name/address/phone
- Verify canonical businesses created in `businesses` table

#### 3. Evidence-Based Enrichment (Free Tier)
- Enable free tier enrichment
- Run enrichment on discovered businesses
- Verify only allowlisted domains scraped
- Verify evidence stored in `evidence` table (URL, excerpt, hash, timestamp)
- Verify enrichments stored in `enrichments` table (field, value, confidence)
- Verify `unknown`/`needs_review` output when data ambiguous

#### 4. Evidence-Based Enrichment (Paid Tier)
- Enable paid tier + add Proxycurl API key
- Run enrichment on discovered businesses
- Verify Proxycurl called with evidence tracking
- Verify fallback chain: Proxycurl → Clearbit → website → needs_review
- Verify confidence scores calculated correctly (0.0000-1.0000)
- Verify match scores computed (0-100 based on criteria)

#### 5. Filtering & Ranking
- Compile prompt criteria into structured filter spec JSON
- Apply hard filters (employee count 50-500, founded > 2015)
- Verify businesses below hard filters excluded
- Verify match scores computed for remaining businesses
- Test configurable weights (employee: 0.4, years: 0.3, location: 0.2, industry: 0.1)
- Verify results sorted by match score (highest first)

#### 6. Evidence Viewer & Audit Trail
- Click on a business result
- Verify evidence viewer shows all enrichments
- Verify each enrichment shows: source, timestamp, confidence, excerpt
- Verify raw HTML link available (if within retention period)
- Verify audit log shows: run created, enrichments added, exports executed

#### 7. Prompt Versioning
- Create prompt v1 with criteria (employee count: 50-500)
- Modify prompt to v2 (employee count: 100-1000)
- Verify version history shows v1 + v2 with timestamps
- Verify runs reference pinned version (immutable)
- Restore from v1 → verify criteria reverted

#### 8. Google Sheets Export (All Modes)
- **Mode 1: Append to one long tab**
  - Export run 1 → tab "All Results"
  - Export run 2 → verify appended to same tab
  - Verify idempotent upsert (no duplicates)

- **Mode 2: Separate tabs per run**
  - Export run 1 → creates tab "Run 1"
  - Export run 2 → creates tab "Run 2"
  - Verify separate tabs with correct data

- **Mode 3: Separate tabs per zip**
  - Export results → verify one tab per zip code (94102, 94103, etc.)
  - Verify tab names match zip codes

- **Mode 4: New spreadsheet per run**
  - Export run → creates new spreadsheet
  - Verify spreadsheet URL accessible

- **Column mapping & Options**
  - Export with custom column mapping (Business Name, Address, Phone)
  - Verify only mapped columns exported
  - Verify column headers match mapping

#### 9. Budget Enforcement & Guardrails
- Create run with budgets: max 100 requests, max $10 cost
- Verify run terminates when budget exceeded
- Verify user notified with budget exceeded reason
- Verify audit log shows budget exceeded event
- Test per-business budgets (max 3 enrichment attempts)
- Verify circuit breaker disables failing API after 5 consecutive failures
- Verify Redis caching reduces redundant API calls

#### 10. BYO API Keys
- Add Proxycurl API key for user
- Verify key encrypted at rest
- Run discovery with BYO key
- Verify precedence rules (paid > free)
- Check usage tracking (last used, usage count)

### Performance Testing

- **Single discovery**: 50-mile radius completes in <10 minutes
- **Concurrent runs**: 5 runs simultaneously without performance degradation
- **Large export**: 10,000 rows export in <5 minutes
- **Database load**: Queries remain fast with 100K+ canonical businesses

### Security Testing

- **Authentication**: Test login, logout, token refresh, OAuth flow
- **API security**: Verify unauthorized requests rejected (401/403)
- **Input validation**: Test SQL injection, XSS prevention
- **Rate limiting**: Verify per-user rate limits enforced
- **API key encryption**: Verify keys encrypted at rest, never logged

---

## Documentation

### Supporting Documents

1. **gpt-plan.md** (149 lines) - Product Requirements Document (PRD)
2. **glm-plan.md** (1,096 lines) - Detailed Technical Implementation Plan
3. **frontend.md** - Complete Frontend Specification
4. **GOOGLE_SHEETS_INTEGRATION.md** - Google OAuth Connection Guide
5. **IMPLEMENTATION_PLAN.md** - This file (combined plan)

### Source Files

- **`/frontend/` folder** - 25 HTML mockups with Tailwind CSS + PNG screenshots
- **`/business_finder/` folder** - Root project directory

---

## Summary

This plan provides:

✅ **Complete HTML mockups** in `/frontend/` folder (25 screens with code.html and screen.png)
✅ **PNG screenshots** showing visual design for each screen
✅ **Tailwind CSS styling** with custom color scheme (#136dec primary)
✅ **Evidence-first enrichment** with source tracking
✅ **Operational guardrails** (budgets, circuit breakers, caching)
✅ **Google Sheets integration** with OAuth2 flow
✅ **BYO provider option** (users bring own API keys)
✅ **9 implementation phases** spanning 14 weeks
✅ **Complete verification plan** with testing checklist

**Implementation Strategy**: Use HTML files in `/frontend/` folder as source of truth for React component conversion. Each HTML file includes complete styling with Tailwind CSS classes and Material Symbols icons. Convert to React components using shadcn/ui primitives.

**Ready to build!** 🚀
