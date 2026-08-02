package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"html/template"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/shopspring/decimal"
	"golang.org/x/crypto/bcrypt"
)

type App struct {
	db                                       *sql.DB
	baseURL, metalsKey, finnhubKey, rapidKey string
	secure                                   bool
	client                                   *http.Client
	tpl                                      *template.Template
	attempts                                 map[string][]time.Time
	mu                                       sync.Mutex
}
type ctxKey int

const userKey ctxKey = 1

type User struct {
	ID                       int64
	Username, CSRF, Currency string
}
type InvestmentType struct {
	ID     int64
	Name   string
	Active bool
}
type Asset struct {
	ID, TypeID                                                                         int64
	Name, Symbol, TypeName, Unit, QuoteCurrency, PricingMode, Provider, ProviderSymbol string
	Scale                                                                              int
	Active                                                                             bool
	Price, PriceSource, PriceAt                                                        string
}
type Transaction struct {
	ID, AssetID                                               int64
	AssetName, Kind, Quantity, Price, Currency, FX, At, Notes string
}
type Holding struct {
	Asset
	Quantity, AverageCost, CostBasisIDR, RealizedIDR, NetInvested, PriceIDR, ValueIDR, UnrealizedIDR, Percent decimal.Decimal
}
type Snapshot struct {
	At    string
	Value decimal.Decimal
}
type Allocation struct {
	Name           string
	Value, Percent decimal.Decimal
}
type TickerCheck struct {
	Symbol, Description, Type string
}
type Page struct {
	Title, View, CSRF, Message, Error, Currency, RefreshKey, RefreshStatus, RefreshAt, TickerProvider string
	User                                                                                              *User
	Types                                                                                             []InvestmentType
	Assets                                                                                            []Asset
	Transactions                                                                                      []Transaction
	Holdings                                                                                          []Holding
	Top                                                                                               []Holding
	TypeAlloc, AssetAlloc                                                                             []Allocation
	Snapshots                                                                                         []Snapshot
	Asset                                                                                             *Asset
	Tx                                                                                                *Transaction
	Tickers                                                                                           []TickerCheck
	Total, NetInvested, Realized, Unrealized, Return                                                  decimal.Decimal
}

func NewApp(path, baseURL, metalsKey, finnhubKey, rapidKey string) (*App, error) {
	db, err := sql.Open("sqlite3", "file:"+path+"?_foreign_keys=on&_journal_mode=WAL&_busy_timeout=5000&_txlock=immediate")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	if _, err = db.Exec(migrations); err != nil {
		db.Close()
		return nil, err
	}
	a := &App{db: db, baseURL: baseURL, metalsKey: metalsKey, finnhubKey: finnhubKey, rapidKey: rapidKey, secure: strings.HasPrefix(baseURL, "https://"), client: &http.Client{Timeout: 6 * time.Second}, attempts: map[string][]time.Time{}}
	a.tpl, err = template.New("page").Funcs(template.FuncMap{"money": formatMoney, "amount": formatAmount, "humanTime": formatTime, "add1": func(i int) int { return i + 1 }, "dec": func(d decimal.Decimal) string { return d.StringFixed(2) }, "pct": func(d decimal.Decimal) string { return d.StringFixed(2) + "%" }, "positive": func(d decimal.Decimal) bool { return d.IsPositive() }, "negative": func(d decimal.Decimal) bool { return d.IsNegative() }}).Parse(pageTemplate)
	if err != nil {
		db.Close()
		return nil, err
	}
	return a, nil
}
func (a *App) Close() error { return a.db.Close() }

func (a *App) Routes() http.Handler {
	m := http.NewServeMux()
	m.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"status":"ok"}`)
	})
	m.HandleFunc("GET /static/app.css", css)
	m.HandleFunc("GET /static/app.js", js)
	m.Handle("GET /static/webawesome/", webAwesomeHandler())
	m.HandleFunc("GET /setup", a.setupGet)
	m.HandleFunc("POST /setup", a.setupPost)
	m.HandleFunc("GET /login", a.loginGet)
	m.HandleFunc("POST /login", a.loginPost)
	m.HandleFunc("POST /logout", a.auth(a.csrf(a.logout)))
	m.HandleFunc("GET /", a.auth(a.dashboard))
	m.HandleFunc("GET /history", a.auth(a.history))
	m.HandleFunc("POST /refresh", a.auth(a.csrf(a.refresh)))
	m.HandleFunc("GET /assets", a.auth(a.assets))
	m.HandleFunc("GET /assets/new", a.auth(a.assetForm))
	m.HandleFunc("POST /assets", a.auth(a.csrf(a.assetSave)))
	m.HandleFunc("GET /assets/{id}", a.auth(a.assetDetail))
	m.HandleFunc("GET /assets/{id}/edit", a.auth(a.assetForm))
	m.HandleFunc("POST /assets/{id}", a.auth(a.csrf(a.assetSave)))
	m.HandleFunc("POST /assets/{id}/archive", a.auth(a.csrf(a.assetArchive)))
	m.HandleFunc("GET /transactions", a.auth(a.transactions))
	m.HandleFunc("GET /transactions/new", a.auth(a.transactionForm))
	m.HandleFunc("POST /transactions", a.auth(a.csrf(a.transactionSave)))
	m.HandleFunc("GET /transactions/{id}", a.auth(a.transactionDetail))
	m.HandleFunc("POST /transactions/{id}/delete", a.auth(a.csrf(a.transactionDelete)))
	m.HandleFunc("GET /settings", a.auth(a.settings))
	m.HandleFunc("GET /settings/tickers", a.auth(a.tickerCheck))
	m.HandleFunc("POST /settings/currency", a.auth(a.csrf(a.currencySave)))
	m.HandleFunc("POST /settings/types", a.auth(a.csrf(a.typeSave)))
	m.HandleFunc("POST /settings/types/{id}", a.auth(a.csrf(a.typeSave)))
	m.HandleFunc("POST /settings/types/{id}/archive", a.auth(a.csrf(a.typeArchive)))
	m.HandleFunc("GET /api/assets/{id}/price", a.auth(a.priceLookup))
	m.HandleFunc("GET /api/exchange-rate", a.auth(a.exchangeRateLookup))
	return a.security(m)
}

func (a *App) security(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Referrer-Policy", "same-origin")
		w.Header().Set("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'")
		next.ServeHTTP(w, r)
	})
}
func (a *App) auth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		c, err := r.Cookie("sipd_session")
		if err != nil {
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}
		h := sha256.Sum256([]byte(c.Value))
		var u User
		var expires string
		err = a.db.QueryRow(`SELECT u.id,u.username,s.csrf_token,s.expires_at,us.display_currency FROM sessions s JOIN users u ON u.id=s.user_id JOIN user_settings us ON us.user_id=u.id WHERE s.id_hash=?`, hex.EncodeToString(h[:])).Scan(&u.ID, &u.Username, &u.CSRF, &expires, &u.Currency)
		if err != nil || expires <= time.Now().UTC().Format(time.RFC3339) {
			a.clearCookie(w)
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}
		next(w, r.WithContext(context.WithValue(r.Context(), userKey, &u)))
	}
}
func (a *App) csrf(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		u := current(r)
		if r.FormValue("csrf_token") != u.CSRF {
			http.Error(w, "Invalid CSRF token", http.StatusForbidden)
			return
		}
		next(w, r)
	}
}
func current(r *http.Request) *User { return r.Context().Value(userKey).(*User) }

func (a *App) setupGet(w http.ResponseWriter, r *http.Request) {
	if a.userExists() {
		http.Redirect(w, r, "/login", http.StatusSeeOther)
		return
	}
	token := a.anonToken(w, r)
	a.render(w, Page{Title: "First-run setup", View: "setup", CSRF: token})
}
func (a *App) setupPost(w http.ResponseWriter, r *http.Request) {
	if !a.validAnonCSRF(r) {
		http.Error(w, "Invalid CSRF token", http.StatusForbidden)
		return
	}
	if a.userExists() {
		http.Error(w, "Setup is disabled", http.StatusForbidden)
		return
	}
	user := strings.TrimSpace(r.FormValue("username"))
	pass := r.FormValue("password")
	if len(user) < 3 || len(user) > 64 || len(pass) < 12 {
		a.render(w, Page{Title: "First-run setup", View: "setup", Error: "Username must be 3–64 characters and password at least 12 characters."})
		return
	}
	h, err := bcrypt.GenerateFromPassword([]byte(pass), bcrypt.DefaultCost)
	if err != nil {
		a.fail(w, err)
		return
	}
	tx, err := a.db.Begin()
	if err != nil {
		a.fail(w, err)
		return
	}
	defer tx.Rollback()
	res, err := tx.Exec(`INSERT INTO users(username,password_hash) VALUES(?,?)`, user, string(h))
	if err != nil {
		http.Error(w, "Setup already completed", http.StatusConflict)
		return
	}
	uid, _ := res.LastInsertId()
	if _, err = tx.Exec(`INSERT INTO user_settings(user_id) VALUES(?)`, uid); err != nil {
		a.fail(w, err)
		return
	}
	for _, n := range []string{"Cash", "Gold", "Money Market", "Stocks", "BTC"} {
		if _, err = tx.Exec(`INSERT INTO investment_types(user_id,name) VALUES(?,?)`, uid, n); err != nil {
			a.fail(w, err)
			return
		}
	}
	if err = tx.Commit(); err != nil {
		a.fail(w, err)
		return
	}
	a.newSession(w, r, uid)
	http.Redirect(w, r, "/", http.StatusSeeOther)
}
func (a *App) userExists() bool {
	var n int
	a.db.QueryRow(`SELECT count(*) FROM users`).Scan(&n)
	return n > 0
}
func (a *App) loginGet(w http.ResponseWriter, r *http.Request) {
	if !a.userExists() {
		http.Redirect(w, r, "/setup", http.StatusSeeOther)
		return
	}
	token := a.anonToken(w, r)
	a.render(w, Page{Title: "Login", View: "login", CSRF: token})
}
func (a *App) loginPost(w http.ResponseWriter, r *http.Request) {
	if !a.validAnonCSRF(r) {
		http.Error(w, "Invalid CSRF token", http.StatusForbidden)
		return
	}
	ip, _, _ := net.SplitHostPort(r.RemoteAddr)
	if !a.allow(ip) {
		http.Error(w, "Too many login attempts. Try again later.", http.StatusTooManyRequests)
		return
	}
	var id int64
	var hash string
	err := a.db.QueryRow(`SELECT id,password_hash FROM users WHERE username=? COLLATE NOCASE`, strings.TrimSpace(r.FormValue("username"))).Scan(&id, &hash)
	if err != nil || bcrypt.CompareHashAndPassword([]byte(hash), []byte(r.FormValue("password"))) != nil {
		time.Sleep(250 * time.Millisecond)
		a.render(w, Page{Title: "Login", View: "login", Error: "Invalid username or password."})
		return
	}
	a.newSession(w, r, id)
	http.Redirect(w, r, "/", http.StatusSeeOther)
}
func (a *App) allow(ip string) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	cut := time.Now().Add(-15 * time.Minute)
	v := a.attempts[ip][:0]
	for _, t := range a.attempts[ip] {
		if t.After(cut) {
			v = append(v, t)
		}
	}
	if len(v) >= 10 {
		a.attempts[ip] = v
		return false
	}
	a.attempts[ip] = append(v, time.Now())
	return true
}
func (a *App) newSession(w http.ResponseWriter, r *http.Request, uid int64) {
	token := random(32)
	csrf := random(24)
	h := sha256.Sum256([]byte(token))
	exp := time.Now().UTC().Add(24 * time.Hour)
	a.db.Exec(`DELETE FROM sessions WHERE expires_at<?`, time.Now().UTC().Format(time.RFC3339))
	_, err := a.db.Exec(`INSERT INTO sessions(id_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,?)`, hex.EncodeToString(h[:]), uid, csrf, exp.Format(time.RFC3339))
	if err != nil {
		a.fail(w, err)
		return
	}
	http.SetCookie(w, &http.Cookie{Name: "sipd_session", Value: token, Path: "/", HttpOnly: true, Secure: a.secure, SameSite: http.SameSiteLaxMode, Expires: exp, MaxAge: 86400})
}
func (a *App) logout(w http.ResponseWriter, r *http.Request) {
	c, _ := r.Cookie("sipd_session")
	if c != nil {
		h := sha256.Sum256([]byte(c.Value))
		a.db.Exec(`DELETE FROM sessions WHERE id_hash=?`, hex.EncodeToString(h[:]))
	}
	a.clearCookie(w)
	http.Redirect(w, r, "/login", http.StatusSeeOther)
}
func (a *App) clearCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{Name: "sipd_session", Path: "/", HttpOnly: true, Secure: a.secure, SameSite: http.SameSiteLaxMode, MaxAge: -1})
}
func (a *App) anonToken(w http.ResponseWriter, r *http.Request) string {
	if c, err := r.Cookie("sipd_csrf"); err == nil && len(c.Value) >= 32 {
		return c.Value
	}
	token := random(24)
	http.SetCookie(w, &http.Cookie{Name: "sipd_csrf", Value: token, Path: "/", HttpOnly: true, Secure: a.secure, SameSite: http.SameSiteStrictMode, MaxAge: 1800})
	return token
}
func (a *App) validAnonCSRF(r *http.Request) bool {
	c, err := r.Cookie("sipd_csrf")
	form := r.FormValue("csrf_token")
	return err == nil && c.Value != "" && len(c.Value) == len(form) && subtle.ConstantTimeCompare([]byte(c.Value), []byte(form)) == 1
}

func (a *App) dashboard(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	hs, rate, err := a.holdings(u.ID)
	if err != nil {
		a.fail(w, err)
		return
	}
	p := Page{Title: "Dashboard", View: "dashboard", User: u, CSRF: u.CSRF, Currency: u.Currency, Holdings: hs, RefreshKey: random(18)}
	for _, h := range hs {
		p.Total = p.Total.Add(h.ValueIDR)
		p.NetInvested = p.NetInvested.Add(h.NetInvested)
		p.Realized = p.Realized.Add(h.RealizedIDR)
		p.Unrealized = p.Unrealized.Add(h.UnrealizedIDR)
	}
	typeTotals := map[string]decimal.Decimal{}
	for _, h := range hs {
		typeTotals[h.TypeName] = typeTotals[h.TypeName].Add(h.ValueIDR)
		p.AssetAlloc = append(p.AssetAlloc, Allocation{Name: h.Name, Value: h.ValueIDR})
	}
	for name, value := range typeTotals {
		p.TypeAlloc = append(p.TypeAlloc, Allocation{Name: name, Value: value})
	}
	for i := range p.TypeAlloc {
		if p.Total.IsPositive() {
			p.TypeAlloc[i].Percent = p.TypeAlloc[i].Value.Div(p.Total).Mul(decimal.NewFromInt(100))
		}
	}
	for i := range p.AssetAlloc {
		if p.Total.IsPositive() {
			p.AssetAlloc[i].Percent = p.AssetAlloc[i].Value.Div(p.Total).Mul(decimal.NewFromInt(100))
		}
	}
	sort.Slice(p.TypeAlloc, func(i, j int) bool { return p.TypeAlloc[i].Value.GreaterThan(p.TypeAlloc[j].Value) })
	sort.Slice(p.AssetAlloc, func(i, j int) bool { return p.AssetAlloc[i].Value.GreaterThan(p.AssetAlloc[j].Value) })
	p.Top = append(p.Top, hs...)
	sort.Slice(p.Top, func(i, j int) bool { return p.Top[i].ValueIDR.GreaterThan(p.Top[j].ValueIDR) })
	if len(p.Top) > 3 {
		p.Top = p.Top[:3]
	}
	if p.Total.IsPositive() {
		for i := range p.Top {
			p.Top[i].Percent = p.Top[i].ValueIDR.Div(p.Total).Mul(decimal.NewFromInt(100))
		}
	}
	if p.NetInvested.IsPositive() {
		p.Return = p.Realized.Add(p.Unrealized).Div(p.NetInvested).Mul(decimal.NewFromInt(100))
	}
	rows, _ := a.db.Query(`SELECT created_at,total_value_idr FROM portfolio_snapshots WHERE user_id=? ORDER BY created_at DESC LIMIT 10`, u.ID)
	if rows != nil {
		defer rows.Close()
		for rows.Next() {
			var s Snapshot
			var v string
			rows.Scan(&s.At, &v)
			s.Value = mustDec(v)
			p.Snapshots = append(p.Snapshots, s)
		}
	}
	a.db.QueryRow(`SELECT status,error_summary,created_at FROM price_refreshes WHERE user_id=? ORDER BY id DESC LIMIT 1`, u.ID).Scan(&p.RefreshStatus, &p.Message, &p.RefreshAt)
	if u.Currency == "USD" && rate.IsPositive() {
		p.Total = p.Total.Div(rate)
		p.NetInvested = p.NetInvested.Div(rate)
		p.Realized = p.Realized.Div(rate)
		p.Unrealized = p.Unrealized.Div(rate)
		for i := range p.Holdings {
			p.Holdings[i].ValueIDR = p.Holdings[i].ValueIDR.Div(rate)
			p.Holdings[i].UnrealizedIDR = p.Holdings[i].UnrealizedIDR.Div(rate)
		}
		for i := range p.Top {
			p.Top[i].ValueIDR = p.Top[i].ValueIDR.Div(rate)
		}
		for i := range p.TypeAlloc {
			p.TypeAlloc[i].Value = p.TypeAlloc[i].Value.Div(rate)
		}
		for i := range p.AssetAlloc {
			p.AssetAlloc[i].Value = p.AssetAlloc[i].Value.Div(rate)
		}
		for i := range p.Snapshots {
			p.Snapshots[i].Value = p.Snapshots[i].Value.Div(rate)
		}
	}
	a.render(w, p)
}

func (a *App) history(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	_, rate, err := a.holdings(u.ID)
	if err != nil {
		a.fail(w, err)
		return
	}
	p := Page{Title: "Portfolio History", View: "history", User: u, CSRF: u.CSRF, Currency: u.Currency}
	rows, err := a.db.Query(`SELECT created_at,total_value_idr FROM portfolio_snapshots WHERE user_id=? ORDER BY created_at DESC`, u.ID)
	if err != nil {
		a.fail(w, err)
		return
	}
	defer rows.Close()
	for rows.Next() {
		var s Snapshot
		var value string
		if err := rows.Scan(&s.At, &value); err != nil {
			a.fail(w, err)
			return
		}
		s.Value = mustDec(value)
		if u.Currency == "USD" && rate.IsPositive() {
			s.Value = s.Value.Div(rate)
		}
		p.Snapshots = append(p.Snapshots, s)
	}
	if err := rows.Err(); err != nil {
		a.fail(w, err)
		return
	}
	a.render(w, p)
}

func (a *App) assets(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	as, err := a.listAssets(u.ID, false)
	if err != nil {
		a.fail(w, err)
		return
	}
	a.render(w, Page{Title: "Assets", View: "assets", User: u, CSRF: u.CSRF, Assets: as, Currency: u.Currency})
}
func (a *App) assetForm(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	p := Page{Title: "Asset", View: "asset_form", User: u, CSRF: u.CSRF}
	p.Types, _ = a.listTypes(u.ID)
	if id := pathID(r); id > 0 {
		v, err := a.getAsset(u.ID, id)
		if err != nil {
			http.NotFound(w, r)
			return
		}
		p.Asset = &v
	} else {
		p.Asset = &Asset{Unit: "unit", QuoteCurrency: "IDR", PricingMode: "manual", Scale: 8, Active: true}
	}
	a.render(w, p)
}
func (a *App) assetSave(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	id := pathID(r)
	typeID, _ := strconv.ParseInt(r.FormValue("type_id"), 10, 64)
	scale, _ := strconv.Atoi(r.FormValue("scale"))
	name := strings.TrimSpace(r.FormValue("name"))
	unit := strings.TrimSpace(r.FormValue("unit"))
	currency := r.FormValue("quote_currency")
	mode := r.FormValue("pricing_mode")
	provider := strings.TrimSpace(r.FormValue("provider"))
	symbol := strings.TrimSpace(r.FormValue("provider_symbol"))
	if provider == "kraken" {
		var ok bool
		symbol, ok = normalizeKrakenPair(symbol)
		if !ok {
			http.Error(w, "Kraken supports BTC/USD only; use BTC, XBT, BTCUSD, or XBTUSD", 400)
			return
		}
	}
	if name == "" || unit == "" || scale < 0 || scale > 12 || !oneOf(currency, "IDR", "USD") || !oneOf(mode, "manual", "automatic", "fixed") {
		http.Error(w, "Invalid asset", 400)
		return
	}
	if mode == "automatic" && !oneOf(provider, "kraken", "metalsdev", "finnhub", "yahoo") {
		http.Error(w, "Unsupported automatic provider", 400)
		return
	}
	if mode == "automatic" && symbol == "" {
		http.Error(w, "Provider symbol is required", 400)
		return
	}
	if oneOf(provider, "finnhub", "yahoo") && (currency != "IDR" || !strings.HasSuffix(strings.ToUpper(symbol), ".JK")) {
		http.Error(w, "Stock providers support Indonesian .JK symbols quoted in IDR only", 400)
		return
	}
	if provider == "metalsdev" && (strings.ToLower(symbol) != "gold" || !oneOf(strings.ToLower(unit), "g", "gram")) {
		http.Error(w, "Metals.dev gold assets must use symbol gold and unit g or gram", 400)
		return
	}
	if id == 0 {
		_, err := a.db.Exec(`INSERT INTO assets(user_id,investment_type_id,name,symbol,unit,quantity_scale,quote_currency,pricing_mode,provider,provider_symbol) SELECT ?,id,?,?,?,?,?,?,?,? FROM investment_types WHERE id=? AND user_id=?`, u.ID, name, r.FormValue("symbol"), unit, scale, currency, mode, provider, symbol, typeID, u.ID)
		if err != nil {
			a.fail(w, err)
			return
		}
	} else {
		res, err := a.db.Exec(`UPDATE assets SET investment_type_id=?,name=?,symbol=?,unit=?,quantity_scale=?,quote_currency=?,pricing_mode=?,provider=?,provider_symbol=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?`, typeID, name, r.FormValue("symbol"), unit, scale, currency, mode, provider, symbol, id, u.ID)
		if err != nil {
			a.fail(w, err)
			return
		}
		if n, _ := res.RowsAffected(); n != 1 {
			http.NotFound(w, r)
			return
		}
	}
	http.Redirect(w, r, "/assets", 303)
}
func (a *App) assetArchive(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	res, _ := a.db.Exec(`UPDATE assets SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?`, pathID(r), u.ID)
	if n, _ := res.RowsAffected(); n != 1 {
		http.NotFound(w, r)
		return
	}
	http.Redirect(w, r, "/assets", 303)
}
func (a *App) assetDetail(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	v, err := a.getAsset(u.ID, pathID(r))
	if err != nil {
		http.NotFound(w, r)
		return
	}
	ts, _ := a.listTransactions(u.ID, v.ID)
	a.render(w, Page{Title: v.Name, View: "asset_detail", User: u, CSRF: u.CSRF, Asset: &v, Transactions: ts, Currency: u.Currency})
}

func (a *App) transactions(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	assetID, _ := strconv.ParseInt(r.URL.Query().Get("asset"), 10, 64)
	ts, err := a.listTransactions(u.ID, assetID)
	if err != nil {
		a.fail(w, err)
		return
	}
	as, _ := a.listAssets(u.ID, true)
	a.render(w, Page{Title: "Transactions", View: "transactions", User: u, CSRF: u.CSRF, Transactions: ts, Assets: as, Currency: u.Currency})
}
func (a *App) transactionForm(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	as, _ := a.listAssets(u.ID, true)
	a.render(w, Page{Title: "New transaction", View: "transaction_form", User: u, CSRF: u.CSRF, Assets: as, RefreshKey: random(18)})
}
func (a *App) transactionSave(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	aid, _ := strconv.ParseInt(r.FormValue("asset_id"), 10, 64)
	kind := r.FormValue("kind")
	q, qe := decimal.NewFromString(r.FormValue("quantity"))
	price, pe := decimal.NewFromString(r.FormValue("price"))
	fx, fe := decimal.NewFromString(r.FormValue("fx_rate"))
	at, ae := time.Parse("2006-01-02T15:04", r.FormValue("occurred_at"))
	asset, err := a.getAsset(u.ID, aid)
	if err != nil || !oneOf(kind, "buy", "sell", "deposit", "withdrawal") || qe != nil || pe != nil || fe != nil || ae != nil || !q.IsPositive() || !price.IsPositive() || !fx.IsPositive() {
		http.Error(w, "Invalid transaction", 400)
		return
	}
	if asset.PricingMode == "fixed" && price.Cmp(decimal.NewFromInt(1)) != 0 {
		http.Error(w, "Cash fixed price must be 1", 400)
		return
	}
	entries, _ := a.ledger(u.ID, aid, 0)
	entries = append(entries, LedgerEntry{Kind: kind, Quantity: q, Price: price, FXToIDR: fx, At: at})
	if _, err = CalculatePosition(entries); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	tx, err := a.db.Begin()
	if err != nil {
		a.fail(w, err)
		return
	}
	defer tx.Rollback()
	_, err = tx.Exec(`INSERT INTO transactions(user_id,asset_id,kind,quantity,unit_price,quote_currency,fx_rate_to_idr,occurred_at,notes,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?)`, u.ID, aid, kind, q.String(), price.String(), asset.QuoteCurrency, fx.String(), at.UTC().Format(time.RFC3339), strings.TrimSpace(r.FormValue("notes")), r.FormValue("idempotency_key"))
	if err != nil {
		if strings.Contains(err.Error(), "UNIQUE") {
			http.Redirect(w, r, "/transactions", 303)
			return
		}
		a.fail(w, err)
		return
	}
	if _, err = tx.Exec(`INSERT INTO asset_prices(user_id,asset_id,price,currency,source,priced_at) VALUES(?,?,?,?,?,?)`, u.ID, aid, price.String(), asset.QuoteCurrency, "Transaction", at.UTC().Format(time.RFC3339)); err != nil {
		a.fail(w, err)
		return
	}
	if err = tx.Commit(); err != nil {
		a.fail(w, err)
		return
	}
	http.Redirect(w, r, "/transactions", 303)
}
func (a *App) transactionDetail(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	var t Transaction
	err := a.db.QueryRow(`SELECT t.id,t.asset_id,a.name,t.kind,t.quantity,t.unit_price,t.quote_currency,t.fx_rate_to_idr,t.occurred_at,t.notes FROM transactions t JOIN assets a ON a.id=t.asset_id WHERE t.id=? AND t.user_id=?`, pathID(r), u.ID).Scan(&t.ID, &t.AssetID, &t.AssetName, &t.Kind, &t.Quantity, &t.Price, &t.Currency, &t.FX, &t.At, &t.Notes)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	a.render(w, Page{Title: "Transaction", View: "transaction_detail", User: u, CSRF: u.CSRF, Tx: &t})
}
func (a *App) transactionDelete(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	id := pathID(r)
	var aid int64
	if a.db.QueryRow(`SELECT asset_id FROM transactions WHERE id=? AND user_id=?`, id, u.ID).Scan(&aid) != nil {
		http.NotFound(w, r)
		return
	}
	entries, _ := a.ledger(u.ID, aid, id)
	if _, err := CalculatePosition(entries); err != nil {
		http.Error(w, "Deletion would make the ledger invalid", 400)
		return
	}
	tx, err := a.db.Begin()
	if err != nil {
		a.fail(w, err)
		return
	}
	defer tx.Rollback()
	if _, err = tx.Exec(`DELETE FROM transactions WHERE id=? AND user_id=?`, id, u.ID); err != nil {
		a.fail(w, err)
		return
	}
	if _, err = tx.Exec(`DELETE FROM asset_prices WHERE user_id=? AND asset_id=? AND source='Transaction'`, u.ID, aid); err != nil {
		a.fail(w, err)
		return
	}
	if _, err = tx.Exec(`INSERT INTO asset_prices(user_id,asset_id,price,currency,source,priced_at) SELECT user_id,asset_id,unit_price,quote_currency,'Transaction',occurred_at FROM transactions WHERE user_id=? AND asset_id=?`, u.ID, aid); err != nil {
		a.fail(w, err)
		return
	}
	if err = tx.Commit(); err != nil {
		a.fail(w, err)
		return
	}
	http.Redirect(w, r, "/transactions", 303)
}

func (a *App) settings(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	types, _ := a.listTypes(u.ID)
	a.render(w, Page{Title: "Settings", View: "settings", User: u, CSRF: u.CSRF, Types: types, Currency: u.Currency})
}
func (a *App) tickerCheck(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	provider := r.URL.Query().Get("provider")
	if provider == "" {
		provider = "yahoo"
	}
	p := Page{Title: "Ticker lookup", View: "ticker_check", User: u, CSRF: u.CSRF, Currency: u.Currency, TickerProvider: provider}
	if !oneOf(provider, "yahoo", "finnhub") {
		p.Error = "Unsupported ticker provider"
		a.render(w, p)
		return
	}
	raw := strings.TrimSpace(r.URL.Query().Get("q"))
	if raw == "" {
		a.render(w, p)
		return
	}
	query, err := normalizeTickerQuery(raw)
	if err != nil {
		p.Error = err.Error()
		a.render(w, p)
		return
	}
	if provider == "yahoo" {
		meta, err := a.yahooHistory(r.Context(), query)
		if err != nil {
			p.Error = "Yahoo Finance RapidAPI: " + err.Error()
		} else {
			p.Tickers = append(p.Tickers, TickerCheck{Symbol: meta.Symbol, Description: meta.LongName, Type: meta.InstrumentType})
		}
	} else {
		matches, err := a.finnhubSearch(r.Context(), query)
		if err != nil {
			p.Error = "Finnhub: " + err.Error()
		} else {
			for _, match := range matches {
				p.Tickers = append(p.Tickers, TickerCheck{Symbol: match.Symbol, Description: match.Description, Type: match.Type})
			}
		}
	}
	if p.Error != "" {
		a.render(w, p)
		return
	}
	if len(p.Tickers) == 0 {
		p.Message = "No matching symbols found."
	}
	a.render(w, p)
}
func (a *App) currencySave(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	c := r.FormValue("currency")
	if !oneOf(c, "IDR", "USD") {
		http.Error(w, "Invalid currency", 400)
		return
	}
	a.db.Exec(`UPDATE user_settings SET display_currency=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=?`, c, u.ID)
	http.Redirect(w, r, "/settings", 303)
}
func (a *App) typeSave(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	id := pathID(r)
	name := strings.TrimSpace(r.FormValue("name"))
	if name == "" {
		http.Error(w, "Name required", 400)
		return
	}
	var err error
	if id == 0 {
		_, err = a.db.Exec(`INSERT INTO investment_types(user_id,name) VALUES(?,?)`, u.ID, name)
	} else {
		_, err = a.db.Exec(`UPDATE investment_types SET name=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?`, name, id, u.ID)
	}
	if err != nil {
		a.fail(w, err)
		return
	}
	http.Redirect(w, r, "/settings", 303)
}
func (a *App) typeArchive(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	id := pathID(r)
	var n int
	a.db.QueryRow(`SELECT count(*) FROM assets WHERE investment_type_id=? AND user_id=?`, id, u.ID).Scan(&n)
	if n > 0 {
		a.db.Exec(`UPDATE investment_types SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?`, id, u.ID)
	} else {
		a.db.Exec(`DELETE FROM investment_types WHERE id=? AND user_id=?`, id, u.ID)
	}
	http.Redirect(w, r, "/settings", 303)
}

type quote struct {
	Price            decimal.Decimal
	Currency, Source string
	At               time.Time
}

func (a *App) priceLookup(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	asset, err := a.getAsset(u.ID, pathID(r))
	if err != nil {
		http.NotFound(w, r)
		return
	}
	q, err := a.quote(r.Context(), asset)
	if err != nil {
		var p, c, s, at string
		err = a.db.QueryRow(`SELECT price,currency,source,priced_at FROM asset_prices WHERE user_id=? AND asset_id=? ORDER BY priced_at DESC LIMIT 1`, u.ID, asset.ID).Scan(&p, &c, &s, &at)
		if err != nil {
			http.Error(w, "No automatic or manual price available", 503)
			return
		}
		json.NewEncoder(w).Encode(map[string]string{"price": p, "currency": c, "source": s + " (last known)", "timestamp": at})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"price": q.Price.String(), "currency": q.Currency, "source": q.Source, "timestamp": q.At.Format(time.RFC3339)})
}
func (a *App) exchangeRateLookup(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	q, err := a.frankfurter(r.Context())
	if err != nil {
		var rate, source, at string
		err = a.db.QueryRow(`SELECT rate,source,priced_at FROM exchange_rates WHERE user_id=? AND base_currency='USD' AND quote_currency='IDR' ORDER BY priced_at DESC LIMIT 1`, u.ID).Scan(&rate, &source, &at)
		if err != nil {
			http.Error(w, "USD/IDR rate unavailable", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"rate": rate, "source": source + " (last known)", "timestamp": at})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"rate": q.Price.String(), "source": q.Source, "timestamp": q.At.Format(time.RFC3339)})
}
func (a *App) refresh(w http.ResponseWriter, r *http.Request) {
	u := current(r)
	key := r.FormValue("refresh_key")
	if key == "" {
		http.Error(w, "Missing refresh key", 400)
		return
	}
	var n int
	a.db.QueryRow(`SELECT count(*) FROM price_refreshes WHERE user_id=? AND refresh_key=?`, u.ID, key).Scan(&n)
	if n > 0 {
		http.Redirect(w, r, "/", 303)
		return
	}
	errs := []string{}
	rate, err := a.frankfurter(r.Context())
	if err != nil {
		errs = append(errs, "USD/IDR: "+err.Error())
	} else {
		a.db.Exec(`INSERT INTO exchange_rates(user_id,base_currency,quote_currency,rate,source,priced_at) VALUES(?,'USD','IDR',?,'Frankfurter',?)`, u.ID, rate.Price.String(), rate.At.Format(time.RFC3339))
	}
	assets, _ := a.listAssets(u.ID, true)
	for _, asset := range assets {
		if asset.PricingMode != "automatic" {
			continue
		}
		q, e := a.quote(r.Context(), asset)
		if e != nil {
			errs = append(errs, asset.Name+": "+e.Error())
			continue
		}
		if !q.Price.IsPositive() {
			errs = append(errs, asset.Name+": provider returned invalid price")
			continue
		}
		a.db.Exec(`INSERT INTO asset_prices(user_id,asset_id,price,currency,source,priced_at) VALUES(?,?,?,?,?,?)`, u.ID, asset.ID, q.Price.String(), q.Currency, q.Source, q.At.Format(time.RFC3339))
	}
	status := "success"
	if len(errs) > 0 {
		status = "partial"
	}
	tx, err := a.db.Begin()
	if err != nil {
		a.fail(w, err)
		return
	}
	defer tx.Rollback()
	_, err = tx.Exec(`INSERT INTO price_refreshes(user_id,refresh_key,status,error_summary) VALUES(?,?,?,?)`, u.ID, key, status, strings.Join(errs, "; "))
	if err != nil {
		http.Redirect(w, r, "/", 303)
		return
	}
	hs, _, err := a.holdingsTx(tx, u.ID)
	if err != nil {
		a.fail(w, err)
		return
	}
	total, netv, realized, unrealized := decimal.Zero, decimal.Zero, decimal.Zero, decimal.Zero
	for _, h := range hs {
		total = total.Add(h.ValueIDR)
		netv = netv.Add(h.NetInvested)
		realized = realized.Add(h.RealizedIDR)
		unrealized = unrealized.Add(h.UnrealizedIDR)
	}
	res, err := tx.Exec(`INSERT INTO portfolio_snapshots(user_id,refresh_key,total_value_idr,net_invested_idr,realized_pl_idr,unrealized_pl_idr) VALUES(?,?,?,?,?,?)`, u.ID, key, total.String(), netv.String(), realized.String(), unrealized.String())
	if err != nil {
		a.fail(w, err)
		return
	}
	sid, _ := res.LastInsertId()
	for _, h := range hs {
		_, err = tx.Exec(`INSERT INTO portfolio_snapshot_items(snapshot_id,user_id,asset_id,quantity,average_cost,price,quote_currency,fx_rate_to_idr,market_value_idr,cost_basis_idr,realized_pl_idr) VALUES(?,?,?,?,?,?,?,?,?,?,?)`, sid, u.ID, h.ID, h.Quantity.String(), h.AverageCost.String(), h.Price, h.QuoteCurrency, h.PriceIDR.Div(nullOne(mustDec(h.Price))).String(), h.ValueIDR.String(), h.CostBasisIDR.String(), h.RealizedIDR.String())
		if err != nil {
			a.fail(w, err)
			return
		}
	}
	if err = tx.Commit(); err != nil {
		a.fail(w, err)
		return
	}
	http.Redirect(w, r, "/", 303)
}

func (a *App) quote(ctx context.Context, v Asset) (quote, error) {
	if v.PricingMode == "fixed" {
		return quote{decimal.NewFromInt(1), v.QuoteCurrency, "Fixed", time.Now().UTC()}, nil
	}
	switch v.Provider {
	case "kraken":
		return a.kraken(ctx, v.ProviderSymbol)
	case "metalsdev":
		return a.metals(ctx, v.ProviderSymbol, v.QuoteCurrency, v.Unit)
	case "finnhub":
		return a.finnhub(ctx, v.ProviderSymbol, v.QuoteCurrency)
	case "yahoo":
		meta, err := a.yahooHistory(ctx, v.ProviderSymbol)
		if err != nil {
			return quote{}, err
		}
		if meta.Currency != v.QuoteCurrency {
			return quote{}, errors.New("Yahoo Finance returned mismatched currency")
		}
		price, err := decimal.NewFromString(meta.RegularMarketPrice.String())
		if err != nil || !price.IsPositive() || meta.RegularMarketTime <= 0 {
			return quote{}, errors.New("Yahoo Finance returned no valid quote")
		}
		return quote{price, meta.Currency, "Yahoo Finance (RapidAPI)", time.Unix(meta.RegularMarketTime, 0).UTC()}, nil
	default:
		return quote{}, errors.New("automatic provider unavailable")
	}
}
func (a *App) kraken(ctx context.Context, symbol string) (quote, error) {
	pair, ok := normalizeKrakenPair(symbol)
	if !ok {
		return quote{}, errors.New("unsupported Kraken asset pair")
	}
	var out struct {
		Error  []string `json:"error"`
		Result map[string]struct {
			C []string `json:"c"`
		} `json:"result"`
	}
	if err := a.getJSON(ctx, "https://api.kraken.com/0/public/Ticker?pair="+url.QueryEscape(pair), &out); err != nil {
		return quote{}, err
	}
	if len(out.Error) > 0 {
		return quote{}, errors.New(strings.Join(out.Error, ","))
	}
	for _, v := range out.Result {
		if len(v.C) == 0 {
			break
		}
		p, e := decimal.NewFromString(v.C[0])
		return quote{p, "USD", "Kraken", time.Now().UTC()}, e
	}
	return quote{}, errors.New("ticker missing")
}

func normalizeKrakenPair(symbol string) (string, bool) {
	switch strings.ToUpper(strings.TrimSpace(symbol)) {
	case "BTC", "XBT", "BTCUSD", "XBTUSD", "XXBTZUSD":
		return "XBTUSD", true
	default:
		return "", false
	}
}
func (a *App) metals(ctx context.Context, symbol, currency, unit string) (quote, error) {
	if a.metalsKey == "" {
		return quote{}, errors.New("Metals.dev API key not configured")
	}
	var out struct {
		Status, Currency, Unit string
		Metals                 map[string]json.Number `json:"metals"`
	}
	providerUnit := strings.ToLower(unit)
	if providerUnit == "gram" {
		providerUnit = "g"
	}
	endpoint := "https://api.metals.dev/v1/latest?api_key=" + url.QueryEscape(a.metalsKey) + "&currency=" + url.QueryEscape(currency) + "&unit=" + url.QueryEscape(providerUnit) + "&metal=" + url.QueryEscape(strings.ToLower(symbol))
	if err := a.getJSON(ctx, endpoint, &out); err != nil {
		return quote{}, err
	}
	if out.Status != "success" || out.Currency != currency || out.Unit != providerUnit {
		return quote{}, errors.New("Metals.dev returned mismatched currency or unit")
	}
	p, e := decimal.NewFromString(out.Metals[strings.ToLower(symbol)].String())
	if e != nil || !p.IsPositive() {
		return quote{}, errors.New("Metals.dev returned no valid metal price")
	}
	return quote{p, currency, "Metals.dev", time.Now().UTC()}, nil
}
func (a *App) finnhub(ctx context.Context, symbol, currency string) (quote, error) {
	if a.finnhubKey == "" {
		return quote{}, errors.New("Finnhub API key not configured")
	}
	var out struct {
		Current   json.Number `json:"c"`
		Timestamp int64       `json:"t"`
		Error     string      `json:"error"`
	}
	endpoint := "https://finnhub.io/api/v1/quote?symbol=" + url.QueryEscape(symbol) + "&token=" + url.QueryEscape(a.finnhubKey)
	if err := a.getJSON(ctx, endpoint, &out); err != nil {
		return quote{}, err
	}
	if out.Error != "" {
		return quote{}, errors.New(out.Error)
	}
	price, err := decimal.NewFromString(out.Current.String())
	if err != nil || !price.IsPositive() || out.Timestamp <= 0 {
		return quote{}, errors.New("Finnhub returned no valid quote")
	}
	return quote{price, currency, "Finnhub", time.Unix(out.Timestamp, 0).UTC()}, nil
}

type yahooHistoryMeta struct {
	Currency           string      `json:"currency"`
	Symbol             string      `json:"symbol"`
	InstrumentType     string      `json:"instrumentType"`
	LongName           string      `json:"longName"`
	RegularMarketPrice json.Number `json:"regularMarketPrice"`
	RegularMarketTime  int64       `json:"regularMarketTime"`
	Status             int         `json:"status"`
}

func (a *App) yahooHistory(ctx context.Context, symbol string) (yahooHistoryMeta, error) {
	if a.rapidKey == "" {
		return yahooHistoryMeta{}, errors.New("RapidAPI key not configured")
	}
	var out struct {
		Meta yahooHistoryMeta `json:"meta"`
	}
	endpoint := "https://yahoo-finance15.p.rapidapi.com/api/v1/markets/stock/history?symbol=" + url.QueryEscape(strings.ToUpper(symbol)) + "&interval=1d&diffandsplits=false"
	headers := map[string]string{"x-rapidapi-host": "yahoo-finance15.p.rapidapi.com", "x-rapidapi-key": a.rapidKey}
	if err := a.getJSONHeaders(ctx, endpoint, &out, headers); err != nil {
		return yahooHistoryMeta{}, err
	}
	if out.Meta.Status != 200 || out.Meta.Symbol == "" || !strings.EqualFold(out.Meta.Symbol, symbol) {
		return yahooHistoryMeta{}, errors.New("Yahoo Finance returned no matching symbol")
	}
	return out.Meta, nil
}

type finnhubSymbol struct {
	Description   string `json:"description"`
	DisplaySymbol string `json:"displaySymbol"`
	Symbol        string `json:"symbol"`
	Type          string `json:"type"`
}

func (a *App) finnhubSearch(ctx context.Context, query string) ([]finnhubSymbol, error) {
	if a.finnhubKey == "" {
		return nil, errors.New("Finnhub API key not configured")
	}
	var out struct {
		Count  int
		Result []finnhubSymbol
	}
	endpoint := "https://finnhub.io/api/v1/search?q=" + url.QueryEscape(query) + "&token=" + url.QueryEscape(a.finnhubKey)
	if err := a.getJSON(ctx, endpoint, &out); err != nil {
		return nil, err
	}
	if len(out.Result) > 25 {
		out.Result = out.Result[:25]
	}
	return out.Result, nil
}
func (a *App) frankfurter(ctx context.Context) (quote, error) {
	var out []struct {
		Date, Base, Quote string
		Rate              json.Number
	}
	if err := a.getJSON(ctx, "https://api.frankfurter.dev/v2/rates?base=USD&quotes=IDR", &out); err != nil {
		return quote{}, err
	}
	if len(out) != 1 {
		return quote{}, errors.New("rate missing")
	}
	p, e := decimal.NewFromString(out[0].Rate.String())
	at, _ := time.Parse("2006-01-02", out[0].Date)
	return quote{p, "IDR", "Frankfurter", at}, e
}
func (a *App) getJSON(ctx context.Context, endpoint string, dst any) error {
	return a.getJSONHeaders(ctx, endpoint, dst, nil)
}

func (a *App) getJSONHeaders(ctx context.Context, endpoint string, dst any, headers map[string]string) error {
	var last error
	for i := 0; i < 2; i++ {
		req, _ := http.NewRequestWithContext(ctx, "GET", endpoint, nil)
		req.Header.Set("User-Agent", "SIP-D/1.0")
		for key, value := range headers {
			req.Header.Set(key, value)
		}
		resp, err := a.client.Do(req)
		if err != nil {
			last = err
			continue
		}
		if resp.StatusCode != 200 {
			io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
			resp.Body.Close()
			last = fmt.Errorf("provider HTTP %d", resp.StatusCode)
			continue
		}
		dec := json.NewDecoder(io.LimitReader(resp.Body, 1<<20))
		dec.UseNumber()
		err = dec.Decode(dst)
		resp.Body.Close()
		if err == nil {
			return nil
		}
		last = err
	}
	return last
}

func (a *App) listTypes(uid int64) ([]InvestmentType, error) {
	rows, err := a.db.Query(`SELECT id,name,active FROM investment_types WHERE user_id=? ORDER BY active DESC,name`, uid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []InvestmentType
	for rows.Next() {
		var v InvestmentType
		rows.Scan(&v.ID, &v.Name, &v.Active)
		out = append(out, v)
	}
	return out, rows.Err()
}
func (a *App) listAssets(uid int64, activeOnly bool) ([]Asset, error) {
	return a.listAssetsFrom(a.db, uid, activeOnly)
}
func (a *App) listAssetsFrom(db queryer, uid int64, activeOnly bool) ([]Asset, error) {
	q := `SELECT a.id,a.investment_type_id,a.name,coalesce(a.symbol,''),t.name,a.unit,a.quantity_scale,a.quote_currency,a.pricing_mode,coalesce(a.provider,''),coalesce(a.provider_symbol,''),a.active,coalesce((SELECT price FROM asset_prices p WHERE p.asset_id=a.id AND p.user_id=a.user_id ORDER BY p.priced_at DESC LIMIT 1),''),coalesce((SELECT source FROM asset_prices p WHERE p.asset_id=a.id AND p.user_id=a.user_id ORDER BY p.priced_at DESC LIMIT 1),''),coalesce((SELECT priced_at FROM asset_prices p WHERE p.asset_id=a.id AND p.user_id=a.user_id ORDER BY p.priced_at DESC LIMIT 1),'') FROM assets a JOIN investment_types t ON t.id=a.investment_type_id WHERE a.user_id=?`
	if activeOnly {
		q += ` AND a.active=1`
	}
	q += ` ORDER BY a.active DESC,t.name,a.name`
	rows, err := db.Query(q, uid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Asset
	for rows.Next() {
		var v Asset
		rows.Scan(&v.ID, &v.TypeID, &v.Name, &v.Symbol, &v.TypeName, &v.Unit, &v.Scale, &v.QuoteCurrency, &v.PricingMode, &v.Provider, &v.ProviderSymbol, &v.Active, &v.Price, &v.PriceSource, &v.PriceAt)
		out = append(out, v)
	}
	return out, rows.Err()
}
func (a *App) getAsset(uid, id int64) (Asset, error) {
	var v Asset
	err := a.db.QueryRow(`SELECT a.id,a.investment_type_id,a.name,coalesce(a.symbol,''),t.name,a.unit,a.quantity_scale,a.quote_currency,a.pricing_mode,coalesce(a.provider,''),coalesce(a.provider_symbol,''),a.active,coalesce((SELECT price FROM asset_prices p WHERE p.asset_id=a.id AND p.user_id=a.user_id ORDER BY p.priced_at DESC LIMIT 1),''),coalesce((SELECT source FROM asset_prices p WHERE p.asset_id=a.id AND p.user_id=a.user_id ORDER BY p.priced_at DESC LIMIT 1),''),coalesce((SELECT priced_at FROM asset_prices p WHERE p.asset_id=a.id AND p.user_id=a.user_id ORDER BY p.priced_at DESC LIMIT 1),'') FROM assets a JOIN investment_types t ON t.id=a.investment_type_id WHERE a.id=? AND a.user_id=?`, id, uid).Scan(&v.ID, &v.TypeID, &v.Name, &v.Symbol, &v.TypeName, &v.Unit, &v.Scale, &v.QuoteCurrency, &v.PricingMode, &v.Provider, &v.ProviderSymbol, &v.Active, &v.Price, &v.PriceSource, &v.PriceAt)
	return v, err
}
func (a *App) listTransactions(uid, asset int64) ([]Transaction, error) {
	q := `SELECT t.id,t.asset_id,a.name,t.kind,t.quantity,t.unit_price,t.quote_currency,t.fx_rate_to_idr,t.occurred_at,t.notes FROM transactions t JOIN assets a ON a.id=t.asset_id WHERE t.user_id=?`
	args := []any{uid}
	if asset > 0 {
		q += ` AND t.asset_id=?`
		args = append(args, asset)
	}
	q += ` ORDER BY t.occurred_at DESC,t.id DESC`
	rows, err := a.db.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Transaction
	for rows.Next() {
		var t Transaction
		rows.Scan(&t.ID, &t.AssetID, &t.AssetName, &t.Kind, &t.Quantity, &t.Price, &t.Currency, &t.FX, &t.At, &t.Notes)
		out = append(out, t)
	}
	return out, rows.Err()
}
func (a *App) ledger(uid, aid, exclude int64) ([]LedgerEntry, error) {
	return a.ledgerFrom(a.db, uid, aid, exclude)
}
func (a *App) ledgerFrom(db queryer, uid, aid, exclude int64) ([]LedgerEntry, error) {
	rows, err := db.Query(`SELECT id,kind,quantity,unit_price,fx_rate_to_idr,occurred_at FROM transactions WHERE user_id=? AND asset_id=? AND id<>? ORDER BY occurred_at,id`, uid, aid, exclude)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []LedgerEntry
	for rows.Next() {
		var e LedgerEntry
		var q, p, fx, at string
		rows.Scan(&e.ID, &e.Kind, &q, &p, &fx, &at)
		e.Quantity = mustDec(q)
		e.Price = mustDec(p)
		e.FXToIDR = mustDec(fx)
		e.At, _ = time.Parse(time.RFC3339, at)
		out = append(out, e)
	}
	return out, rows.Err()
}
func (a *App) holdings(uid int64) ([]Holding, decimal.Decimal, error) { return a.holdingsTx(a.db, uid) }

type queryer interface {
	Query(string, ...any) (*sql.Rows, error)
	QueryRow(string, ...any) *sql.Row
}

func (a *App) holdingsTx(q queryer, uid int64) ([]Holding, decimal.Decimal, error) {
	rate := decimal.Zero
	var rs string
	q.QueryRow(`SELECT rate FROM exchange_rates WHERE user_id=? AND base_currency='USD' AND quote_currency='IDR' ORDER BY priced_at DESC LIMIT 1`, uid).Scan(&rs)
	if rs != "" {
		rate = mustDec(rs)
	}
	assets, err := a.listAssetsFrom(q, uid, true)
	if err != nil {
		return nil, rate, err
	}
	var out []Holding
	for _, v := range assets {
		entries, err := a.ledgerFrom(q, uid, v.ID, 0)
		if err != nil {
			return nil, rate, err
		}
		p, err := CalculatePosition(entries)
		if err != nil {
			return nil, rate, err
		}
		h := Holding{Asset: v, Quantity: p.Quantity, AverageCost: p.AverageCost, CostBasisIDR: p.CostBasis, RealizedIDR: p.Realized, NetInvested: p.NetInvested}
		price := mustDec(v.Price)
		if v.PricingMode == "fixed" {
			price = decimal.NewFromInt(1)
			h.Price = "1"
		}
		h.PriceIDR = price
		if v.QuoteCurrency == "USD" {
			h.PriceIDR = price.Mul(rate)
		}
		h.ValueIDR = h.Quantity.Mul(h.PriceIDR)
		h.UnrealizedIDR = h.ValueIDR.Sub(h.CostBasisIDR)
		out = append(out, h)
	}
	return out, rate, nil
}

func (a *App) render(w http.ResponseWriter, p Page) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := a.tpl.Execute(w, p); err != nil {
		slog.Error("render", "error", err)
	}
}
func (a *App) fail(w http.ResponseWriter, err error) {
	slog.Error("request failed", "error", err)
	http.Error(w, "Internal server error", 500)
}
func random(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return base64.RawURLEncoding.EncodeToString(b)
}
func pathID(r *http.Request) int64 { id, _ := strconv.ParseInt(r.PathValue("id"), 10, 64); return id }
func oneOf(s string, v ...string) bool {
	for _, x := range v {
		if s == x {
			return true
		}
	}
	return false
}
func normalizeTickerQuery(raw string) (string, error) {
	s := strings.TrimSpace(raw)
	if len(s) < 1 || len(s) > 64 {
		return "", errors.New("Search must be 1–64 characters")
	}
	for _, r := range s {
		if r < 32 || r == 127 {
			return "", errors.New("Search contains unsupported characters")
		}
	}
	return s, nil
}
func mustDec(s string) decimal.Decimal { d, _ := decimal.NewFromString(s); return d }
func nullOne(d decimal.Decimal) decimal.Decimal {
	if d.IsZero() {
		return decimal.NewFromInt(1)
	}
	return d
}
func formatMoney(d decimal.Decimal, c string) string {
	if c == "IDR" {
		return "Rp " + groupNumber(d.Round(0).StringFixed(0))
	}
	return "$" + groupNumber(d.StringFixed(2))
}

func formatAmount(s string) string {
	d, err := decimal.NewFromString(s)
	if err != nil {
		return s
	}
	return groupNumber(d.String())
}

func groupNumber(s string) string {
	parts := strings.SplitN(s, ".", 2)
	integer, sign := parts[0], ""
	if strings.HasPrefix(integer, "-") {
		sign, integer = "-", integer[1:]
	}
	for i := len(integer) - 3; i > 0; i -= 3 {
		integer = integer[:i] + "." + integer[i:]
	}
	if len(parts) == 2 {
		return sign + integer + "," + parts[1]
	}
	return sign + integer
}

func formatTime(s string) string {
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02 15:04:05", "2006-01-02T15:04"} {
		t, err := time.Parse(layout, s)
		if err == nil {
			return t.Format("02 Jan 2006, 15:04")
		}
	}
	if t, err := time.Parse("2006-01-02", s); err == nil {
		return t.Format("02 Jan 2006")
	}
	return s
}
