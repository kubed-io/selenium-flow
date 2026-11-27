# Signing in to the admin page

The page itself opens without a credential: it is the sign-in card, and every
datum it shows is fetched with one afterwards. A server started without
`AUTH_TOKEN` checks no credential anywhere, the admin page included.

Two credentials open it, and admin is the only role:

| Credential | Who | Named in the audit and control notices as |
|---|---|---|
| The server's token, the same one MCP clients send | whoever holds it | `admin` |
| An OIDC sign-in holding a role in `oidc.admin_roles` | a person with an account at the issuer | their username |

## With the token

Type the token into the `MCP token` field and click **Enter**. The page keeps it
in the tab's `sessionStorage`, never `localStorage`, so closing the tab forgets
it and a reload keeps it. `That token was refused.` means it is not the
server's token. Sign out, in the header, forgets it.

## With OIDC

A **Sign in with OIDC** button sits under the token field when the operator set
up the admin page's OIDC client. It sends the browser to the issuer, which
returns it to the page signed in; no password reaches selenium-flow. Inside a
frame, the button opens the page in a new tab first.

| The card says | Means |
|---|---|
| `Signed in as <name>, who does not hold an admin role.` | the account signed in, but holds none of `oidc.admin_roles`; an operator grants the role at the issuer |
| `The server refused this sign-in.` | the server would not take the issuer's token |
| `The issuer could not be reached.` | the page could not read the issuer's discovery document |
| `The issuer would not complete the sign-in.` | the issuer refused to exchange the code |
| `The issuer said: <reason>.` | the issuer answered with an error |
| `That sign-in reply was not ours; try again.` | a stale or foreign reply; sign in again |
| `Your sign-in ended; sign in again.` | the issuer refused to renew it |

The page renews the sign-in before it expires while it is open. A reload loses
it and tries once, silently, to sign in again with the issuer; when that needs
the person, the card shows with no message. Sign out ends the page's sign-in,
not the person's session at the issuer.

Setting up the issuer, its client and the redirect URI is the operator's:
the `deploying-the-server` skill, *OIDC*.
