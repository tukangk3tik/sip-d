package main

import (
	"errors"
	"sort"
	"time"

	"github.com/shopspring/decimal"
)

type LedgerEntry struct {
	ID       int64
	Kind     string
	Quantity decimal.Decimal
	Price    decimal.Decimal
	FXToIDR  decimal.Decimal
	At       time.Time
}

type Position struct {
	Quantity, AverageCost, CostBasis, Realized, NetInvested decimal.Decimal
}

func CalculatePosition(entries []LedgerEntry) (Position, error) {
	sort.SliceStable(entries, func(i, j int) bool {
		if entries[i].At.Equal(entries[j].At) {
			return entries[i].ID < entries[j].ID
		}
		return entries[i].At.Before(entries[j].At)
	})
	p := Position{}
	for _, e := range entries {
		if !e.Quantity.IsPositive() || !e.Price.IsPositive() || !e.FXToIDR.IsPositive() {
			return p, errors.New("quantity, price, and exchange rate must be positive")
		}
		value := e.Quantity.Mul(e.Price).Mul(e.FXToIDR)
		switch e.Kind {
		case "buy", "deposit":
			p.CostBasis = p.CostBasis.Add(value)
			p.Quantity = p.Quantity.Add(e.Quantity)
			p.AverageCost = p.CostBasis.Div(p.Quantity)
			p.NetInvested = p.NetInvested.Add(value)
		case "sell", "withdrawal":
			if e.Quantity.GreaterThan(p.Quantity) {
				return p, errors.New("quantity exceeds available holding")
			}
			cost := p.AverageCost.Mul(e.Quantity)
			p.Realized = p.Realized.Add(value.Sub(cost))
			p.CostBasis = p.CostBasis.Sub(cost)
			p.Quantity = p.Quantity.Sub(e.Quantity)
			p.NetInvested = p.NetInvested.Sub(value)
			if p.Quantity.IsZero() {
				p.AverageCost, p.CostBasis = decimal.Zero, decimal.Zero
			}
		default:
			return p, errors.New("invalid transaction type")
		}
	}
	return p, nil
}

func Convert(amount, usdToIDR decimal.Decimal, from, to string) (decimal.Decimal, error) {
	if from == to {
		return amount, nil
	}
	if !usdToIDR.IsPositive() {
		return decimal.Zero, errors.New("USD/IDR rate unavailable")
	}
	if from == "USD" && to == "IDR" {
		return amount.Mul(usdToIDR), nil
	}
	if from == "IDR" && to == "USD" {
		return amount.Div(usdToIDR), nil
	}
	return decimal.Zero, errors.New("unsupported currency")
}
