package main

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/shopspring/decimal"
	"golang.org/x/crypto/bcrypt"
)

func testApp(t *testing.T) *App {
	t.Helper()
	a, err := NewApp(t.TempDir()+"/test.db", "", "", "", "")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { a.Close() })
	return a
}

func TestRoutesBuildAndServeEmbeddedWebAwesome(t *testing.T) {
	a := testApp(t)
	for _, path := range []string{"/static/webawesome/styles/webawesome.css"} {
		w := httptest.NewRecorder()
		a.Routes().ServeHTTP(w, httptest.NewRequest("GET", path, nil))
		if w.Code != http.StatusOK || w.Body.Len() == 0 {
			t.Fatalf("embedded UI asset %s: %d", path, w.Code)
		}
	}
}

func TestAuthenticatedPageUsesAdminShell(t *testing.T) {
	a := testApp(t)
	var page bytes.Buffer
	if err := a.tpl.Execute(&page, Page{Title: "Dashboard", View: "dashboard", User: &User{Username: "owner"}, Currency: "IDR", TypeAlloc: []Allocation{{Name: "Stocks", Value: decimal.NewFromInt(750000), Percent: decimal.NewFromInt(75)}}, AssetAlloc: []Allocation{{Name: "BBCA", Value: decimal.NewFromInt(500000), Percent: decimal.NewFromInt(50)}}, Top: []Holding{{Asset: Asset{Name: "BBCA"}, ValueIDR: decimal.NewFromInt(500000), Percent: decimal.NewFromInt(50)}}}); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"admin-shell", "sidebar-nav", `id="nav-toggle"`, `id="nav-backdrop"`} {
		if !strings.Contains(page.String(), want) {
			t.Fatalf("authenticated layout missing %q", want)
		}
	}
	if strings.Contains(page.String(), "alpine") || strings.Contains(page.String(), "x-data") {
		t.Fatal("authenticated layout still loads Alpine")
	}
	transactions := strings.Index(page.String(), `href="/transactions">Transactions`)
	assets := strings.Index(page.String(), `href="/assets">Assets`)
	if transactions < 0 || assets < 0 || transactions > assets {
		t.Fatal("Transactions must appear before Assets in navigation")
	}
	if !strings.Contains(page.String(), `<progress max="100" value="75"`) || !strings.Contains(page.String(), "allocation-list") {
		t.Fatal("dashboard allocation progress is missing")
	}
	if !strings.Contains(page.String(), "largest-list") || !strings.Contains(page.String(), "50.00% of portfolio") {
		t.Fatal("largest assets ranking is missing")
	}
}

func TestCurrencyFormattingUsesDotThousandsSeparator(t *testing.T) {
	if got := formatMoney(decimal.RequireFromString("1234567.89"), "IDR"); got != "Rp 1.234.568" {
		t.Fatalf("IDR format: %s", got)
	}
	if got := formatMoney(decimal.RequireFromString("1234567.89"), "USD"); got != "$1.234.567,89" {
		t.Fatalf("USD format: %s", got)
	}
	if got := formatAmount("16250.5"); got != "16.250,5" {
		t.Fatalf("amount format: %s", got)
	}
}

func TestHumanReadableTime(t *testing.T) {
	for raw, want := range map[string]string{
		"2026-08-01T12:48:12Z": "01 Aug 2026, 12:48",
		"2026-08-01 12:48:12":  "01 Aug 2026, 12:48",
		"2026-08-01":           "01 Aug 2026",
	} {
		if got := formatTime(raw); got != want {
			t.Fatalf("formatTime(%q) = %q, want %q", raw, got, want)
		}
	}
}
func postForm(path string, values url.Values) *http.Request {
	r := httptest.NewRequest("POST", path, strings.NewReader(values.Encode()))
	r.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	return r
}
func anonPost(path string, values url.Values) *http.Request {
	values.Set("csrf_token", "test-csrf")
	r := postForm(path, values)
	r.AddCookie(&http.Cookie{Name: "sipd_csrf", Value: "test-csrf"})
	return r
}
func setupUser(t *testing.T, a *App, name string) int64 {
	t.Helper()
	h, _ := bcrypt.GenerateFromPassword([]byte("correct horse battery staple"), bcrypt.MinCost)
	res, err := a.db.Exec(`INSERT INTO users(username,password_hash) VALUES(?,?)`, name, h)
	if err != nil {
		t.Fatal(err)
	}
	id, _ := res.LastInsertId()
	a.db.Exec(`INSERT INTO user_settings(user_id) VALUES(?)`, id)
	return id
}

func TestFirstUserSetupLocks(t *testing.T) {
	a := testApp(t)
	w := httptest.NewRecorder()
	a.setupPost(w, anonPost("/setup", url.Values{"username": {"owner"}, "password": {"correct horse battery staple"}}))
	if w.Code != http.StatusSeeOther {
		t.Fatalf("setup status %d: %s", w.Code, w.Body.String())
	}
	var hash string
	if err := a.db.QueryRow(`SELECT password_hash FROM users WHERE username='owner'`).Scan(&hash); err != nil {
		t.Fatal(err)
	}
	if hash == "correct horse battery staple" || bcrypt.CompareHashAndPassword([]byte(hash), []byte("correct horse battery staple")) != nil {
		t.Fatal("password was not bcrypt hashed")
	}
	w = httptest.NewRecorder()
	a.loginPost(w, anonPost("/login", url.Values{"username": {"owner"}, "password": {"correct horse battery staple"}}))
	if w.Code != http.StatusSeeOther || len(w.Result().Cookies()) == 0 {
		t.Fatal("valid authentication did not create a session")
	}
	w = httptest.NewRecorder()
	a.setupPost(w, anonPost("/setup", url.Values{"username": {"second"}, "password": {"another secure password"}}))
	if w.Code != http.StatusForbidden {
		t.Fatalf("second setup status %d", w.Code)
	}
}

func TestAnonymousCSRFTokenSurvivesRepeatedSetupViews(t *testing.T) {
	a := testApp(t)
	first := httptest.NewRecorder()
	token1 := a.anonToken(first, httptest.NewRequest("GET", "/setup", nil))
	cookies := first.Result().Cookies()
	if len(cookies) != 1 {
		t.Fatal("CSRF cookie missing")
	}
	secondReq := httptest.NewRequest("GET", "/setup", nil)
	secondReq.AddCookie(cookies[0])
	second := httptest.NewRecorder()
	token2 := a.anonToken(second, secondReq)
	if token1 != token2 {
		t.Fatal("repeated setup view rotated CSRF token")
	}
	if len(second.Result().Cookies()) != 0 {
		t.Fatal("existing CSRF cookie was unnecessarily replaced")
	}
}

func TestOwnershipBoundary(t *testing.T) {
	a := testApp(t)
	u1 := setupUser(t, a, "one")
	u2 := setupUser(t, a, "two")
	r, _ := a.db.Exec(`INSERT INTO investment_types(user_id,name) VALUES(?, 'Cash')`, u1)
	tid, _ := r.LastInsertId()
	r, _ = a.db.Exec(`INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(?,?,'Private','unit','IDR','manual')`, u1, tid)
	aid, _ := r.LastInsertId()
	if _, err := a.getAsset(u2, aid); err == nil {
		t.Fatal("second user accessed first user's asset")
	}
}

func TestWeightedAverageAndValidation(t *testing.T) {
	at := time.Now()
	p, err := CalculatePosition([]LedgerEntry{{ID: 1, Kind: "buy", Quantity: decimal.NewFromInt(2), Price: decimal.NewFromInt(100), FXToIDR: decimal.NewFromInt(1), At: at}, {ID: 2, Kind: "buy", Quantity: decimal.NewFromInt(2), Price: decimal.NewFromInt(200), FXToIDR: decimal.NewFromInt(1), At: at.Add(time.Second)}, {ID: 3, Kind: "sell", Quantity: decimal.NewFromInt(1), Price: decimal.NewFromInt(180), FXToIDR: decimal.NewFromInt(1), At: at.Add(2 * time.Second)}})
	if err != nil {
		t.Fatal(err)
	}
	if !p.Quantity.Equal(decimal.NewFromInt(3)) || !p.AverageCost.Equal(decimal.NewFromInt(150)) || !p.Realized.Equal(decimal.NewFromInt(30)) || !p.CostBasis.Equal(decimal.NewFromInt(450)) {
		t.Fatalf("unexpected position: %+v", p)
	}
	_, err = CalculatePosition([]LedgerEntry{{Kind: "withdrawal", Quantity: decimal.NewFromInt(1), Price: decimal.NewFromInt(1), FXToIDR: decimal.NewFromInt(1)}})
	if err == nil {
		t.Fatal("over-withdraw accepted")
	}
	_, err = CalculatePosition([]LedgerEntry{{Kind: "buy", Quantity: decimal.Zero, Price: decimal.NewFromInt(1), FXToIDR: decimal.NewFromInt(1)}})
	if err == nil {
		t.Fatal("zero quantity accepted")
	}
}

func TestConversionAndProfit(t *testing.T) {
	v, err := Convert(decimal.NewFromInt(2), decimal.NewFromInt(16000), "USD", "IDR")
	if err != nil || !v.Equal(decimal.NewFromInt(32000)) {
		t.Fatalf("conversion %s %v", v, err)
	}
	p, _ := CalculatePosition([]LedgerEntry{{Kind: "buy", Quantity: decimal.NewFromInt(2), Price: decimal.NewFromInt(10), FXToIDR: decimal.NewFromInt(16000)}})
	market := decimal.NewFromInt(12).Mul(decimal.NewFromInt(16000)).Mul(p.Quantity)
	if !market.Sub(p.CostBasis).Equal(decimal.NewFromInt(64000)) {
		t.Fatal("incorrect unrealized P/L")
	}
}

func TestExchangeRateLookupDoesNotCreateSnapshot(t *testing.T) {
	a := testApp(t)
	uid := setupUser(t, a, "owner")
	a.client = &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`[{"date":"2026-08-01","base":"USD","quote":"IDR","rate":16250}]`)), Header: make(http.Header)}, nil
	})}
	req := httptest.NewRequest("GET", "/api/exchange-rate", nil).WithContext(context.WithValue(context.Background(), userKey, &User{ID: uid}))
	w := httptest.NewRecorder()
	a.exchangeRateLookup(w, req)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"rate":"16250"`) {
		t.Fatalf("lookup %d: %s", w.Code, w.Body.String())
	}
	var n int
	a.db.QueryRow(`SELECT count(*) FROM portfolio_snapshots`).Scan(&n)
	if n != 0 {
		t.Fatal("exchange-rate lookup created snapshot")
	}
}

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func TestManualRefreshSnapshotAndPartialFailure(t *testing.T) {
	a := testApp(t)
	uid := setupUser(t, a, "owner")
	r, _ := a.db.Exec(`INSERT INTO investment_types(user_id,name) VALUES(?,'BTC')`, uid)
	tid, _ := r.LastInsertId()
	r, _ = a.db.Exec(`INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,provider,provider_symbol) VALUES(?,?,'Bitcoin','BTC','USD','automatic','kraken','XBTUSD')`, uid, tid)
	aid, _ := r.LastInsertId()
	a.db.Exec(`INSERT INTO asset_prices(user_id,asset_id,price,currency,source,priced_at) VALUES(?,?,'50000','USD','old','2025-01-01T00:00:00Z')`, uid, aid)
	a.client = &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
		body := `{"error":["temporary"]}`
		if strings.Contains(r.URL.Host, "frankfurter") {
			body = `[{"date":"2026-07-31","base":"USD","quote":"IDR","rate":16000}]`
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), Header: make(http.Header)}, nil
	})}
	u := &User{ID: uid, CSRF: "csrf", Currency: "IDR"}
	req := postForm("/refresh", url.Values{"csrf_token": {"csrf"}, "refresh_key": {"once"}}).WithContext(context.WithValue(context.Background(), userKey, u))
	w := httptest.NewRecorder()
	a.refresh(w, req)
	if w.Code != http.StatusSeeOther {
		t.Fatalf("refresh status %d: %s", w.Code, w.Body.String())
	}
	var price string
	a.db.QueryRow(`SELECT price FROM asset_prices WHERE user_id=? AND asset_id=? ORDER BY id DESC LIMIT 1`, uid, aid).Scan(&price)
	if price != "50000" {
		t.Fatalf("failed provider replaced price with %s", price)
	}
	var snapshots int
	a.db.QueryRow(`SELECT count(*) FROM portfolio_snapshots WHERE user_id=?`, uid).Scan(&snapshots)
	if snapshots != 1 {
		t.Fatalf("snapshot count %d", snapshots)
	}
	w = httptest.NewRecorder()
	a.refresh(w, req)
	a.db.QueryRow(`SELECT count(*) FROM portfolio_snapshots WHERE user_id=?`, uid).Scan(&snapshots)
	if snapshots != 1 {
		t.Fatal("double submission duplicated snapshot")
	}
}

func TestFinnhubQuote(t *testing.T) {
	a := testApp(t)
	a.finnhubKey = "test-key"
	a.client = &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
		if r.URL.Query().Get("symbol") != "BBCA.JK" || r.URL.Query().Get("token") != "test-key" {
			t.Fatal("incorrect Finnhub request")
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`{"c":9150,"t":1785556800}`)), Header: make(http.Header)}, nil
	})}
	q, err := a.finnhub(context.Background(), "BBCA.JK", "IDR")
	if err != nil {
		t.Fatal(err)
	}
	if !q.Price.Equal(decimal.NewFromInt(9150)) || q.Currency != "IDR" || q.Source != "Finnhub" {
		t.Fatalf("unexpected quote: %+v", q)
	}
	a.client = &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`{"c":0,"t":0}`)), Header: make(http.Header)}, nil
	})}
	if _, err = a.finnhub(context.Background(), "BBCA.JK", "IDR"); err == nil {
		t.Fatal("invalid zero quote accepted")
	}
}

func TestMetalsDevGoldUsesAssetCurrencyAndUnit(t *testing.T) {
	a := testApp(t)
	a.metalsKey = "test-key"
	a.client = &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
		q := r.URL.Query()
		if q.Get("currency") != "IDR" || q.Get("unit") != "g" || q.Get("metal") != "gold" {
			t.Fatalf("incorrect Metals.dev query: %v", q)
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`{"status":"success","currency":"IDR","unit":"g","metals":{"gold":2343080.2902}}`)), Header: make(http.Header)}, nil
	})}
	q, err := a.metals(context.Background(), "gold", "IDR", "gram")
	if err != nil {
		t.Fatal(err)
	}
	if q.Currency != "IDR" || !q.Price.Equal(decimal.RequireFromString("2343080.2902")) {
		t.Fatalf("unexpected quote: %+v", q)
	}
}

func TestNormalizeTickerQuery(t *testing.T) {
	if got, err := normalizeTickerQuery("  crude oil  "); err != nil || got != "crude oil" {
		t.Fatalf("unexpected query %q %v", got, err)
	}
	if _, err := normalizeTickerQuery("\n"); err == nil {
		t.Fatal("empty query accepted")
	}
}

func TestFinnhubTickerSearch(t *testing.T) {
	a := testApp(t)
	a.finnhubKey = "test-key"
	a.client = &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`{"count":1,"result":[{"description":"BANK CENTRAL ASIA TBK PT","displaySymbol":"BBCA.JK","symbol":"BBCA.JK","type":"Common Stock"}]}`)), Header: make(http.Header)}, nil
	})}
	v, err := a.finnhubSearch(context.Background(), "BBCA")
	if err != nil || len(v) != 1 || v[0].Symbol != "BBCA.JK" || v[0].Description == "" {
		t.Fatalf("unexpected search: %+v %v", v, err)
	}
}

func TestYahooRapidAPIQuote(t *testing.T) {
	a := testApp(t)
	a.rapidKey = "test-key"
	a.client = &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
		if r.URL.Query().Get("symbol") != "BBCA.JK" || r.Header.Get("x-rapidapi-key") != "test-key" || r.Header.Get("x-rapidapi-host") != "yahoo-finance15.p.rapidapi.com" {
			t.Fatal("incorrect Yahoo RapidAPI request")
		}
		body := `{"meta":{"currency":"IDR","symbol":"BBCA.JK","instrumentType":"EQUITY","longName":"PT Bank Central Asia Tbk","regularMarketPrice":6325,"regularMarketTime":1785489296,"status":200},"body":{}}`
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), Header: make(http.Header)}, nil
	})}
	q, err := a.quote(context.Background(), Asset{PricingMode: "automatic", Provider: "yahoo", ProviderSymbol: "BBCA.JK", QuoteCurrency: "IDR"})
	if err != nil || !q.Price.Equal(decimal.NewFromInt(6325)) || q.Currency != "IDR" || q.Source != "Yahoo Finance (RapidAPI)" {
		t.Fatalf("unexpected Yahoo quote: %+v %v", q, err)
	}
}

func TestKrakenNormalizesBTCAlias(t *testing.T) {
	a := testApp(t)
	a.client = &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
		if r.URL.Query().Get("pair") != "XBTUSD" {
			t.Fatalf("unexpected Kraken pair %q", r.URL.Query().Get("pair"))
		}
		body := `{"error":[],"result":{"XXBTZUSD":{"c":["63032.60000","0.1"]}}}`
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), Header: make(http.Header)}, nil
	})}
	q, err := a.kraken(context.Background(), "BTC")
	if err != nil || !q.Price.Equal(decimal.RequireFromString("63032.6")) {
		t.Fatalf("unexpected Kraken quote: %+v %v", q, err)
	}
}

func TestTransactionDoesNotCreateSnapshot(t *testing.T) {
	a := testApp(t)
	uid := setupUser(t, a, "owner")
	r, _ := a.db.Exec(`INSERT INTO investment_types(user_id,name) VALUES(?,'Cash')`, uid)
	tid, _ := r.LastInsertId()
	r, _ = a.db.Exec(`INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(?,?,'Cash','IDR','IDR','fixed')`, uid, tid)
	aid, _ := r.LastInsertId()
	u := &User{ID: uid, CSRF: "x"}
	req := postForm("/transactions", url.Values{"asset_id": {decimal.NewFromInt(aid).String()}, "kind": {"deposit"}, "quantity": {"100"}, "price": {"1"}, "fx_rate": {"1"}, "occurred_at": {"2026-07-31T12:00"}, "idempotency_key": {"tx1"}}).WithContext(context.WithValue(context.Background(), userKey, u))
	w := httptest.NewRecorder()
	a.transactionSave(w, req)
	var n int
	a.db.QueryRow(`SELECT count(*) FROM portfolio_snapshots`).Scan(&n)
	if n != 0 {
		t.Fatal("transaction created snapshot")
	}
}

func TestAssetCreationAndOwnership(t *testing.T) {
	a := testApp(t)
	uid := setupUser(t, a, "owner")
	other := setupUser(t, a, "other")
	r, _ := a.db.Exec(`INSERT INTO investment_types(user_id,name) VALUES(?,'Stocks')`, uid)
	tid, _ := r.LastInsertId()
	u := &User{ID: uid, CSRF: "x"}
	req := postForm("/assets", url.Values{"name": {"Bank Central Asia"}, "symbol": {"BBCA.JK"}, "type_id": {decimal.NewFromInt(tid).String()}, "unit": {"share"}, "scale": {"0"}, "quote_currency": {"IDR"}, "pricing_mode": {"manual"}}).WithContext(context.WithValue(context.Background(), userKey, u))
	w := httptest.NewRecorder()
	a.assetSave(w, req)
	if w.Code != http.StatusSeeOther {
		t.Fatalf("asset create %d: %s", w.Code, w.Body.String())
	}
	var aid int64
	if err := a.db.QueryRow(`SELECT id FROM assets WHERE user_id=? AND symbol='BBCA.JK'`, uid).Scan(&aid); err != nil {
		t.Fatal(err)
	}
	if _, err := a.getAsset(other, aid); err == nil {
		t.Fatal("asset leaked across ownership boundary")
	}
}
