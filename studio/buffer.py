"""Minimal client for Buffer's GraphQL API (https://developers.buffer.com)."""
from __future__ import annotations

import httpx

BUFFER_API_URL = "https://api.buffer.com"
REQUEST_TIMEOUT = 60.0

ORGANIZATIONS_QUERY = "{ account { organizations { id name } } }"
CHANNELS_QUERY = """
query Channels($orgId: OrganizationId!) {
  channels(input: { organizationId: $orgId }) { id name service type isDisconnected }
}"""
POST_QUERY = """
query Post($id: PostId!) { post(input: { id: $id }) { id status dueAt sentAt channelId } }"""
SCHEDULED_QUERY = """
query Scheduled($orgId: OrganizationId!, $channelIds: [ChannelId!]) {
  posts(first: 100, input: { organizationId: $orgId,
        filter: { status: [scheduled], channelIds: $channelIds } }) {
    edges { node { id channelId } }
  }
}"""
EDIT_POST_MUTATION = """
mutation EditPost($input: EditPostInput!) {
  editPost(input: $input) {
    ... on PostActionSuccess { post { id status dueAt } }
    ... on MutationError { message }
  }
}"""
CREATE_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id dueAt } }
    ... on MutationError { message }
  }
}"""


class BufferError(RuntimeError):
    pass


class BufferClient:
    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise BufferError("No Buffer API key. Add BUFFER_API_KEY=... to .env or .env.txt.")
        self._http = httpx.Client(
            base_url=BUFFER_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT,
            transport=transport,
        )

    def _gql(self, query: str, variables: dict | None = None) -> dict:
        try:
            response = self._http.post("", json={"query": query, "variables": variables or {}})
        except httpx.HTTPError as exc:
            raise BufferError(f"Could not reach Buffer: {exc}") from exc
        if response.status_code == 401:
            raise BufferError("Buffer rejected the API key. Create a new one in Buffer > Settings > API.")
        try:
            body = response.json()
        except ValueError as exc:
            raise BufferError(f"Buffer returned HTTP {response.status_code} with no JSON.") from exc
        if body.get("errors"):
            messages = "; ".join(e.get("message", "unknown error") for e in body["errors"])
            raise BufferError(f"Buffer error: {messages}")
        return body.get("data") or {}

    def organizations(self) -> list[dict]:
        return self._gql(ORGANIZATIONS_QUERY).get("account", {}).get("organizations", [])

    def channels(self, org_id: str) -> list[dict]:
        return self._gql(CHANNELS_QUERY, {"orgId": org_id}).get("channels", [])

    def all_channels(self) -> list[dict]:
        return [{**c, "organizationId": org["id"]}
                for org in self.organizations() for c in self.channels(org["id"])]

    def post(self, post_id: str) -> dict | None:
        """The post's id, status, dueAt and sentAt, or None if it no longer exists."""
        return self._gql(POST_QUERY, {"id": post_id}).get("post")

    def scheduled_counts(self, org_id: str, channel_ids: list[str]) -> dict[str, int]:
        data = self._gql(SCHEDULED_QUERY, {"orgId": org_id, "channelIds": channel_ids})
        counts = {cid: 0 for cid in channel_ids}
        for edge in data.get("posts", {}).get("edges", []):
            cid = edge["node"]["channelId"]
            counts[cid] = counts.get(cid, 0) + 1
        return counts

    def schedule_draft(self, post_id: str, due_at: str, content: dict) -> dict:
        """Turns an existing draft into a scheduled post at due_at (ISO UTC).

        Buffer rejects the edit unless the content (text, assets, metadata) is sent again.
        """
        edit = {**content, "id": post_id, "saveToDraft": False, "mode": "customScheduled",
                "schedulingType": "automatic", "dueAt": due_at}
        result = self._gql(EDIT_POST_MUTATION, {"input": edit}).get("editPost") or {}
        if "post" in result:
            return result["post"]
        raise BufferError(f"Buffer refused to schedule {post_id}: "
                          f"{result.get('message', 'no reason given')}")

    def create_post(self, post_input: dict) -> dict:
        result = self._gql(CREATE_POST_MUTATION, {"input": post_input}).get("createPost") or {}
        if "post" in result:
            return result["post"]
        raise BufferError(f"Buffer refused the post: {result.get('message', 'no reason given')}")
