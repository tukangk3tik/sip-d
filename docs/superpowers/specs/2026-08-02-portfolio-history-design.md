# Portfolio History Design

## Goal

Keep the Dashboard concise by showing only the ten most recent portfolio
snapshots, while providing a dedicated page for the complete history.

## Navigation

The complete history remains a dedicated `/history` page, reached only from
the Dashboard's **Portfolio history** section through its **View all history**
link. The authenticated sidebar is unchanged.

## Data Flow

The Dashboard queries the ten newest `portfolio_snapshots` owned by the
current user. A new authenticated `GET /history` handler queries all of that
user's snapshots, ordered newest first. Both handlers apply the user's IDR or
USD display conversion exactly as the existing Dashboard does.

## Presentation

The Dashboard retains its current compact history rows but shows no more than
ten. The complete page uses the same row presentation inside a panel and
shows the existing empty state when no snapshot exists.

## Error Handling and Security

Database errors use the existing application failure path. Every query is
filtered by `user_id`, and the route is protected by the existing
authentication middleware.

## Testing

Tests assert that the Dashboard query is capped at ten snapshots, the history
route is authenticated, user-scoped, ordered newest first, and renders all
available snapshots.
