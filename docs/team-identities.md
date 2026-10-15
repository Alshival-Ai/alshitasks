# People and droids

Open **Team** in the workspace navigation and choose **Create profile**. A profile has a name, a Person or Droid type, and active status. It does not create a login account or require an email address. Existing local contacts retain their IDs and assignments.

Assign a profile as the task assignee or reviewer. These must be different identities. In the profile panel, create a credential with the required workspace permissions and optional expiration. Copy the returned MCP setup. Desktop credentials use the operating system credential store; the self-hosted browser returns a Bearer token once. Credentials never synchronize to the portal.

An external agent can call `project_identity`, then `project_list` with `assignee` or `reviewer` set to the returned opaque identity ID. For example, prompt it to read its assigned open tickets, choose an appropriate task, update progress, and submit the result for review. Assignment alone does not launch an agent. Scheduling and process execution remain outside AlshiTasks.

Local profile credentials can access all local workspace records allowed by their selected scopes. They cannot access connected portal workspaces or manage profiles, credentials, or device settings. Approval of reviewed work requires the separate assigned reviewer and `project:approve`. Standard read and write permissions remain necessary for their respective operations.

Deactivating a profile revokes all of its credentials and prevents new assignments. Old assignments and activity remain readable. Reactivating does not restore revoked credentials. Issue a new credential to resume access. Rotating credentials preserves the profile identity and records which credential performed each action.

Connected workspaces manage profiles through the authenticated portal connection while online, with explicit `team:manage` consent. Existing connections may need to sign in again to grant it. Older portals must advertise `team-profiles-v1` before accepting profile assignments or management. Profile metadata and activity synchronize; secrets do not. When publishing a local board, explicitly map each local assignee and reviewer to a destination member. Create the destination profile in Team first if necessary; names are never used to merge identities.
