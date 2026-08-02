# Portfolio History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show only ten recent portfolio snapshots on the Dashboard and provide an authenticated page for the complete history.

**Architecture:** The Dashboard's existing snapshot query changes from 30 rows to 10. A new authenticated `/history` handler retrieves the complete user-owned snapshot list and renders it through a dedicated template view; shared currency conversion stays consistent with the Dashboard.

**Tech Stack:** Go 1.23, `net/http`, SQLite, Go `html/template`, existing `app_test.go` integration tests.

## Global Constraints

- All snapshot queries filter by the authenticated `user_id` and order by `created_at DESC`.
- The Dashboard shows exactly at most ten latest snapshots.
- Complete history is available only through authenticated `GET /history`.
- Both views use the user-selected IDR or USD currency conversion.

---

### Task 1: Add complete history route and Dashboard limit

**Files:**
- Modify: `app.go`
- Modify: `ui.go`
- Modify: `app_test.go`

**Interfaces:**
- Consumes: authenticated `User` from `current(r)` and `portfolio_snapshots` rows.
- Produces: `GET /history` page with all user-owned snapshots and Dashboard data with a ten-row maximum.

- [ ] **Step 1: Write failing route and rendering tests**

Add integration tests that insert more than ten snapshots for an owner and a snapshot for a second user. Assert `GET /` renders only the ten latest owner values and a `View all history` link. Assert authenticated `GET /history` renders every owner value newest first, excludes the second user's value, and the sidebar exposes `/history`.

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `go test ./... -run 'TestDashboardHistoryLimit|TestPortfolioHistory'`
Expected: FAIL because the Dashboard exposes more than ten rows and `/history` is not registered.

- [ ] **Step 3: Implement the minimal route, data retrieval, and navigation**

Register `GET /history` behind `a.auth`. Limit the Dashboard snapshot query to 10. Add a history handler that queries all rows for the current user, applies the existing USD conversion behavior, and renders a `history` view. Add the sidebar and Dashboard links plus a compact history panel in the template.

- [ ] **Step 4: Run focused tests and full suite**

Run: `go test ./... -run 'TestDashboardHistoryLimit|TestPortfolioHistory' && go test ./... && go vet ./...`
Expected: all tests pass with no vet findings.

- [ ] **Step 5: Commit**

Commit the application, template, and test changes with message `feat: add complete portfolio history view`.
