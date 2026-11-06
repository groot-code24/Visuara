# How each kind of user uses Visuara

Everyone signs in the same way, with an emailed link or code or with Google, at `/login`. No password is involved. A new person then sets up in three steps: their name, then their workspace (its name and what it is for), then a plan. Anyone who was invited joins the inviting workspace instead.

A **workspace** belongs to a company, brand or studio. The **kind** chosen at sign-up decides which pages it gets, and the **role** decides what each person in it may do. One person can belong to several workspaces and switch between them in Organizations.

## 1. By workspace kind

### Just my own videos (`direct`)
For creators and in-house marketers who place their own products in their own footage.
1. **Library:** upload two to four product images (a transparent PNG looks best) and the videos.
2. **New decision:** choose products and videos. The decision engine finds flat surfaces (walls, signs, screens, tabletops), tracks them, scores every product on each one, and explains its choice.
3. **Decisions → Apply:** turns a recommendation into a draft placement.
4. **Placements:** run the frame-by-frame quality check, then a manager approves. Any change creates a new version that needs approval again.
5. **Renders:** render the video and download it.

### A publisher or studio (`publisher`): sells scenes
1. **Library:** upload videos and record the rights to them (consent, channels, dates).
2. **Analyses:** analyse each video into scenes and placement opportunities.
3. **Inventory:** list the scenes brands may buy, with prices and brand-safety rules.
4. **Placement requests:** approve or decline what brands ask for. Approving confirms the reservation and issues the quote to the brand.
5. **Placements:** produce the accepted placement (quality check, approval, render) and deliver it.
6. **Finance:** see your share of each paid invoice; payouts are recorded by the platform team.

### A brand (`brand`): buys scenes
1. **Library:** upload your products.
2. **Marketplace:** browse publishers' scenes that fit your products and targeting.
3. **Campaigns:** set a campaign's products, budget and targeting, then submit it.
4. **Placement requests:** reserve scenes, request placements, accept quotes, and accept or reject the delivered video.
5. **Finance:** pay invoices online (Pay → the provider's checkout) or by bank transfer.

### An agency (`agency`): works for brands
Everything a brand can do, for several clients:
- **Organizations → Agency access.** A client brand's owner grants the agency access with a role (viewer, editor or manager). The agency then switches into the client's workspace and works there, never above the granted role.
- **Clients as viewers.** Invite a client into your own workspace as a viewer: they see decisions, previews and renders, but can change nothing.
- **Manager approval** is needed before any render.

The owner can change the kind in Organizations until the workspace has marketplace activity.

## 2. By role inside a workspace

| Role | Can |
|---|---|
| Viewer | See everything in the workspace, read-only. Good for clients and stakeholders |
| Editor | Upload products and videos, run decisions and analyses, prepare placements, run campaigns |
| Manager | All of the above, plus approve placements, render, download, and pay invoices |
| Admin | All of the above, plus invite and manage members, and buy, change or cancel the plan |
| Owner | Everything, including the workspace's kind and transferring ownership |

Invite people in **Settings → Team**. Every member uses a seat; the plan sets how many seats there are.

## 3. By plan

| | Free | Pro | Agency | Enterprise |
|---|---|---|---|---|
| Price | $0 | $39 a month, or $29 a month billed $348 yearly | $159 a month, or $119 a month billed $1,428 yearly | Custom, invoiced |
| Decisions a month | 5 | 60 | 300 | Custom |
| Rendered videos a month | 3 | 40 | 200 | Custom |
| Seats | 1 | 3 | 15 | Custom |
| Storage | 2 GB | 50 GB | 250 GB | Custom |

- **Buying a plan.** Choose Pro or Agency on the last onboarding step, or later in **Settings → Plan**. Payment happens on Lemon Squeezy's checkout, by card (or PayPal, if it is switched on in the store), and the workspace switches plan as soon as Lemon Squeezy confirms, usually within seconds.
- **Switching, cancelling, card and invoices.** **Switch plan** in Settings is prorated by Lemon Squeezy. **Manage billing** opens Lemon Squeezy's customer portal, for cancelling, changing the card and downloading invoices. A cancelled plan stays active until the end of the paid period, then the workspace returns to Free.
- **Who can pay.** Only the owner or an admin can buy or change the plan. An agency working inside a client's workspace cannot change that workspace's plan.

## 4. Platform administrators (the Visuara team)
**Platform admin** in the menu, for people flagged as platform administrators:
- every organization and its plan;
- marking invoices paid by bank transfer;
- publisher payouts;
- the ledger's trial balance;
- payment and subscription webhook events, and which ones need a person.

Day-to-day operations are in [DEPLOYMENT_RUNBOOK.md](../engineering/DEPLOYMENT_RUNBOOK.md).
