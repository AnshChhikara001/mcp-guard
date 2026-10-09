# Host on one Oracle Always Free VM, with Postgres on Neon

The Agent and the Gateway (with the six fake bank MCP servers inside its container, reachable only from it) run with Docker Compose on one Oracle Cloud Always Free Ampere VM (2 OCPU, 12 GB, arm64), behind Caddy for HTTPS. Postgres is Neon's free tier, the Next.js frontend is on Vercel and traces go to Langfuse Cloud. Card-free hosts capped a service at 512 MB and slept after 15 minutes, which would have meant shrinking the models and one-minute cold starts; the Always Free VM never sleeps and can't charge the card unless the account is upgraded to Pay As You Go, which we never do.

## Considered Options

- **Render free + Neon + Vercel (no card):** 512 MB per service and cold starts; the Gateway's PII and injection models don't fit without quantising them.
- **Azure for Students:** no card and can't charge, but scale-to-zero cold starts and $100 a year of credit. Kept as the fallback if Oracle rejects the card or has no capacity.
- **Postgres on the VM:** fewer services, but Neon is simpler to start with and keeps data off the one machine Oracle could reclaim.

## Consequences

- Images are built for arm64.
- Oracle can change Always Free terms (it halved the Ampere allowance in June 2026), so containers stay portable and nothing provider-specific goes into the code.
- The VM is hardened as part of deployment: SSH keys only, firewall open only for HTTPS, automatic security updates.
