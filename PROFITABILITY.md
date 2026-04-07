## Profitability Report — SOSFiler — 2026-03-19

### Cost Structure
| Cost Item | Type | Amount | Source |
|-----------|------|--------|--------|
| Stripe Fee | Variable | 2.9% + $0.30 | [1] |
| OpenAI GPT-4o Input | Variable | $2.50 / 1M tokens | [2] |
| OpenAI GPT-4o Output| Variable | $10.00 / 1M tokens | [2] |
| SendGrid Essentials | Fixed/Var | $19.95 / 50k emails | [3] |
| DigitalOcean Droplet| Fixed | $48 / month | [4] |
| Porkbun .com Renewal| Fixed | $11.08 / year | [5] |
| Porkbun .biz Renewal| Fixed | $16.99 / year | [5] |
| LLC State Filing Fee (Avg) | Passthrough | $132.00 | [6] |
| DBA Filing Fee (Avg) | Passthrough | $35.00 | [7] |
| License Filing Fee (Avg) | Passthrough | $100.00 | [8] |
| Annual Report Fee (Avg) | Passthrough | $91.00 | [6] |

### Variable Cost Per Unit
| Service | Price | Variable Cost | Margin | Margin % |
|---------|-------|--------------|--------|----------|
| LLC Formation | $49 | $5.82 | $43.18 | 88.1% |
| DBA Filing | $29 | $2.21 | $26.79 | 92.4% |
| License Filing (Base) | $49 | $4.67 | $44.33 | 90.5% |
| License Filing (Premium) | $99 | $6.12 | $92.88 | 93.8% |
| Registered Agent | $49 | $1.72 | $47.28 | 96.5% |
| Annual Report Filing | $25 | $3.67 | $21.33 | 85.3% |

*Variable costs include Stripe fees calculated on total charge (Price + Avg. State Fee) and GPT-4o token usage per task.*

### Fixed Monthly Costs
| Item | Monthly Cost | Source |
|------|-------------|--------|
| Droplet Share (1/3) | $16.00 | [4] |
| Domain Amortization | $2.34 | [5] |
| SendGrid Essentials | $19.95 | [3] |
| **Total** | **$38.29** | |

### Breakeven Analysis
- Fixed monthly costs: $38.29
- Average revenue per customer: $45.20
- Average variable cost per customer: $3.76
- Breakeven customers/month: 1

### P&L Projections (Monthly)
| Customers | Revenue | Variable Costs | Fixed Costs | Profit | Margin % |
|-----------|---------|---------------|-------------|--------|----------|
| 10 | $452.00 | $37.60 | $38.29 | $376.11 | 83.2% |
| 50 | $2,260.00 | $188.00 | $38.29 | $2,033.71 | 90.0% |
| 100 | $4,520.00 | $376.00 | $38.29 | $4,105.71 | 90.8% |
| 500 | $22,600.00 | $1,880.00 | $38.29 | $20,681.71 | 91.5% |
| 1000 | $45,200.00 | $3,760.00 | $38.29 | $41,401.71 | 91.6% |

### Flags
- **Stripe Fee Drag:** Stripe fees are levied on the *total* transaction, including the non-revenue state fee. In high-fee states like Massachusetts ($500), the LLC service margin drops from 88% to 66%.
- **SendGrid Free Tier:** The SendGrid free tier is unreliable for production and has been discontinued as of 2025; calculations use the $19.95/mo Essentials plan for stability.
- **Fixed Cost Threshold:** Fixed costs are exceptionally low ($38.29) due to infrastructure sharing, resulting in a breakeven point of just 1 customer per month.

### Sources
1. [Stripe Pricing](https://stripe.com/pricing)
2. [OpenAI API Pricing](https://openai.com/api/pricing)
3. [SendGrid Pricing](https://sendgrid.com/pricing)
4. [DigitalOcean Droplet Pricing](https://www.digitalocean.com/pricing/droplets)
5. [Porkbun Domain Pricing](https://porkbun.com/products/domains)
6. [LLC State Fee Survey 2024](https://scribecount.com/author-resource/setting-up-publishing-company/llc-filing-fees)
7. [DBA Filing Fee Averages](https://www.tailorbrands.com/blog/dba-cost)
8. [Business License Fee Guide](https://www.marshmallowchallenge.com/blog/how-much-are-business-license-fees-in-2024-your-state-by-state-guide/)
