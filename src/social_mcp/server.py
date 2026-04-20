"""MCP server that exposes Twitter/X and Facebook Pages tools.

Tools are organized by platform with consistent prefixes:
  * ``twitter_*``  — X posts, timelines, search, publish, reply, delete
  * ``facebook_*`` — Facebook Pages list, posts, comments, publish, reply, delete
  * ``auth_*``     — status + logout

Every tool input is a Pydantic model; every output is a JSON string so the
model in the MCP client can parse it structurally. Errors are raised as
``ToolError`` so the client sees them as tool failures rather than malformed
success responses.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from .facebook import FacebookError, get_client as get_fb_client
from .token_store import get_store
from .twitter import TwitterError, get_client as get_tw_client

log = logging.getLogger(__name__)

mcp = FastMCP("social_mcp")


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


def _jsonify(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


async def _run_twitter(coro: Any) -> str:
    try:
        result = await coro
    except TwitterError as e:
        raise ToolError(str(e)) from e
    return _jsonify(result)


async def _run_facebook(coro: Any) -> str:
    try:
        result = await coro
    except FacebookError as e:
        raise ToolError(str(e)) from e
    return _jsonify(result)


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )


# ===========================================================================
# Auth tools
# ===========================================================================


@mcp.tool(
    name="auth_status",
    annotations={
        "title": "Authentication status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def auth_status() -> str:
    """Show which social accounts currently have stored credentials.

    Returns a JSON object with one entry per connected provider. Does NOT
    include the tokens themselves.
    """
    store = get_store()
    out: dict[str, Any] = {}
    for provider in ("twitter", "facebook"):
        cred = store.get(provider)
        out[provider] = (
            None
            if cred is None
            else {
                "connected": True,
                "scope": cred.scope,
                "expires_at": cred.expires_at,
                "is_expired": cred.is_expired(),
            }
        )
    return _jsonify(out)


class LogoutInput(_StrictModel):
    provider: Annotated[str, Field(pattern="^(twitter|facebook)$")]


@mcp.tool(
    name="auth_logout",
    annotations={
        "title": "Disconnect a provider",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def auth_logout(params: LogoutInput) -> str:
    """Delete stored credentials for the given provider.

    You will need to re-authenticate (via the CLI) to use that provider again.
    """
    deleted = get_store().delete(params.provider)
    return _jsonify({"provider": params.provider, "deleted": deleted})


# ===========================================================================
# Twitter tools
# ===========================================================================


@mcp.tool(
    name="twitter_me",
    annotations={
        "title": "Get authenticated X user",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def twitter_me() -> str:
    """Return the X profile that social-mcp is authenticated as."""
    return await _run_twitter(get_tw_client().me())


class TimelineInput(_StrictModel):
    max_results: int = Field(
        default=20,
        ge=5,
        le=100,
        description="How many posts to return (X accepts 5..100).",
    )


@mcp.tool(
    name="twitter_get_home_timeline",
    annotations={
        "title": "Home timeline",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def twitter_get_home_timeline(params: TimelineInput) -> str:
    """Posts from the accounts the authenticated user follows, newest first.

    This is what 'reading follower posts' maps to on X: the reverse-chronological
    timeline of everyone you follow.
    """
    return await _run_twitter(get_tw_client().home_timeline(max_results=params.max_results))


class UserPostsInput(_StrictModel):
    username: Annotated[
        str,
        Field(
            min_length=1,
            max_length=15,
            description="X handle without the '@' (e.g. 'elonmusk').",
        ),
    ]
    max_results: int = Field(default=20, ge=5, le=100)


@mcp.tool(
    name="twitter_get_user_posts",
    annotations={
        "title": "User's recent posts",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def twitter_get_user_posts(params: UserPostsInput) -> str:
    """Recent posts from a specific X user (retweets and replies excluded)."""
    username = params.username.lstrip("@")
    return await _run_twitter(
        get_tw_client().user_posts(username, max_results=params.max_results)
    )


class SearchInput(_StrictModel):
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=512,
            description=(
                "X recent-search query. Supports operators: from:, to:, "
                "#hashtag, lang:en, -filter:retweets, etc. Example: "
                "'from:anthropicai claude -filter:retweets'."
            ),
        ),
    ]
    max_results: int = Field(default=20, ge=10, le=100)


@mcp.tool(
    name="twitter_search_posts",
    annotations={
        "title": "Search recent posts",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def twitter_search_posts(params: SearchInput) -> str:
    """Search posts from the last 7 days matching an X query string."""
    return await _run_twitter(
        get_tw_client().search_posts(params.query, max_results=params.max_results)
    )


class PostIdInput(_StrictModel):
    post_id: Annotated[str, Field(pattern=r"^\d+$", description="Numeric tweet ID.")]


@mcp.tool(
    name="twitter_get_post",
    annotations={
        "title": "Get a single post",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def twitter_get_post(params: PostIdInput) -> str:
    """Look up a single tweet by its numeric ID."""
    return await _run_twitter(get_tw_client().get_post(params.post_id))


class RepliesInput(_StrictModel):
    post_id: Annotated[str, Field(pattern=r"^\d+$")]
    max_results: int = Field(default=20, ge=10, le=100)


@mcp.tool(
    name="twitter_get_replies",
    annotations={
        "title": "Get replies to a post",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def twitter_get_replies(params: RepliesInput) -> str:
    """Fetch replies directed at the author of the given post.

    Note: this uses recent search and therefore only surfaces replies from the
    last 7 days.
    """
    return await _run_twitter(
        get_tw_client().get_replies(params.post_id, max_results=params.max_results)
    )


class CreatePostInput(_StrictModel):
    text: Annotated[
        str,
        Field(
            min_length=1,
            max_length=4000,
            description=(
                "Post text. Standard accounts are limited to 280 characters; "
                "Premium accounts can use up to 4000. X will reject over-long "
                "posts with a clear error."
            ),
        ),
    ]
    reply_to_id: Annotated[
        str | None,
        Field(
            default=None,
            pattern=r"^\d+$",
            description="If provided, the new post becomes a reply to this post ID.",
        ),
    ] = None
    quote_of_id: Annotated[
        str | None,
        Field(
            default=None,
            pattern=r"^\d+$",
            description="If provided, the new post quote-tweets this post ID.",
        ),
    ] = None
    media_ids: Annotated[
        list[str] | None,
        Field(
            default=None,
            max_length=4,
            description=(
                "Optional list of media IDs returned from `twitter_upload_media`. "
                "X allows up to 4 images, or exactly 1 GIF, or exactly 1 video; "
                "mixing types is rejected."
            ),
        ),
    ] = None


@mcp.tool(
    name="twitter_post",
    annotations={
        "title": "Create a post (or reply, or quote)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def twitter_post(params: CreatePostInput) -> str:
    """Publish a new post. Can also be used to reply, quote, or attach media.

    To reply, set ``reply_to_id`` to the target post's ID. To quote, set
    ``quote_of_id``. To attach media, first call ``twitter_upload_media`` for
    each file and pass the returned IDs as ``media_ids``.
    """
    return await _run_twitter(
        get_tw_client().create_post(
            params.text,
            reply_to_id=params.reply_to_id,
            quote_of_id=params.quote_of_id,
            media_ids=params.media_ids,
        )
    )


class UploadMediaInput(_StrictModel):
    file_path: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Absolute local path to an image (.jpg, .png, .gif, .webp) or "
                "video (.mp4, .mov) that the MCP server can read. For videos, "
                "the tool waits until X finishes processing before returning."
            ),
        ),
    ]
    media_category: Annotated[
        str | None,
        Field(
            default=None,
            pattern=r"^(tweet_image|tweet_gif|tweet_video)$",
            description=(
                "Optional override. If omitted, is inferred from the file "
                "extension. Valid values: tweet_image, tweet_gif, tweet_video."
            ),
        ),
    ] = None


@mcp.tool(
    name="twitter_upload_media",
    annotations={
        "title": "Upload an image, GIF, or video",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def twitter_upload_media(params: UploadMediaInput) -> str:
    """Upload a media file to X and return its media_id + media_key.

    Pass the returned ``id`` back to ``twitter_post`` in the ``media_ids``
    array to attach the media to a post. This tool requires the
    ``media.write`` OAuth scope; if you authenticated before v0.2, re-run
    ``social-mcp authenticate twitter`` to pick it up.
    """
    return await _run_twitter(
        get_tw_client().upload_media(
            params.file_path, media_category=params.media_category,
        )
    )


@mcp.tool(
    name="twitter_delete_post",
    annotations={
        "title": "Delete a post",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def twitter_delete_post(params: PostIdInput) -> str:
    """Delete one of your own posts by ID. This cannot be undone."""
    return await _run_twitter(get_tw_client().delete_post(params.post_id))


# ===========================================================================
# Facebook tools (Pages only; Groups API was deprecated April 22, 2024)
# ===========================================================================


@mcp.tool(
    name="facebook_me",
    annotations={
        "title": "Get authenticated Facebook user",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def facebook_me() -> str:
    """Return the Facebook user profile social-mcp is authenticated as."""
    return await _run_facebook(get_fb_client().me())


@mcp.tool(
    name="facebook_list_pages",
    annotations={
        "title": "List managed Pages",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def facebook_list_pages() -> str:
    """List Facebook Pages that the authenticated user can manage.

    Returns an array of ``{id, name, category, tasks}``. ``tasks`` tells you
    what you can do on the Page (``CREATE_CONTENT``, ``MODERATE``, etc.).
    Page access tokens are intentionally not included in this response.
    """
    return await _run_facebook(get_fb_client().list_pages())


class PagePostsInput(_StrictModel):
    page_id: Annotated[str, Field(pattern=r"^\d+$", description="Numeric Page ID.")]
    limit: int = Field(default=20, ge=1, le=100)


@mcp.tool(
    name="facebook_get_page_posts",
    annotations={
        "title": "Get posts on a Page",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def facebook_get_page_posts(params: PagePostsInput) -> str:
    """Return recent posts on one of your Pages, with engagement counts."""
    return await _run_facebook(
        get_fb_client().page_posts(params.page_id, limit=params.limit)
    )


class PagePostInput(_StrictModel):
    page_id: Annotated[str, Field(pattern=r"^\d+$")]
    post_id: Annotated[
        str,
        Field(
            min_length=3,
            max_length=64,
            description="Full post ID (usually '<pageid>_<postid>').",
        ),
    ]


@mcp.tool(
    name="facebook_get_post",
    annotations={
        "title": "Get a single Page post",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def facebook_get_post(params: PagePostInput) -> str:
    """Return one Page post with engagement counters."""
    return await _run_facebook(
        get_fb_client().get_post(params.post_id, page_id=params.page_id)
    )


class PostCommentsInput(_StrictModel):
    page_id: Annotated[str, Field(pattern=r"^\d+$")]
    post_id: Annotated[str, Field(min_length=3, max_length=64)]
    limit: int = Field(default=25, ge=1, le=100)


@mcp.tool(
    name="facebook_get_post_comments",
    annotations={
        "title": "Get comments on a Page post",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def facebook_get_post_comments(params: PostCommentsInput) -> str:
    """Return comments on a Page post, oldest first."""
    return await _run_facebook(
        get_fb_client().post_comments(
            params.post_id, page_id=params.page_id, limit=params.limit,
        )
    )


class PublishInput(_StrictModel):
    page_id: Annotated[str, Field(pattern=r"^\d+$")]
    message: Annotated[str, Field(min_length=1, max_length=63000)]
    link: Annotated[
        str | None,
        Field(
            default=None,
            description="Optional URL to attach (renders as a link preview).",
        ),
    ] = None


@mcp.tool(
    name="facebook_post_to_page",
    annotations={
        "title": "Publish a post to a Page",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def facebook_post_to_page(params: PublishInput) -> str:
    """Publish a new post to one of your Pages.

    Requires ``pages_manage_posts``. Your app must have passed Meta's App Review
    for this permission to work on real (non-test) Pages.
    """
    return await _run_facebook(
        get_fb_client().publish_to_page(params.page_id, params.message, link=params.link)
    )


class CommentInput(_StrictModel):
    page_id: Annotated[str, Field(pattern=r"^\d+$")]
    post_id: Annotated[str, Field(min_length=3, max_length=64)]
    message: Annotated[str, Field(min_length=1, max_length=8000)]


@mcp.tool(
    name="facebook_comment_on_post",
    annotations={
        "title": "Comment on a Page post",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def facebook_comment_on_post(params: CommentInput) -> str:
    """Add a comment, from the Page, to one of the Page's posts."""
    return await _run_facebook(
        get_fb_client().comment_on_post(
            params.post_id, page_id=params.page_id, message=params.message,
        )
    )


class ReplyInput(_StrictModel):
    page_id: Annotated[str, Field(pattern=r"^\d+$")]
    comment_id: Annotated[str, Field(min_length=3, max_length=64)]
    message: Annotated[str, Field(min_length=1, max_length=8000)]


@mcp.tool(
    name="facebook_reply_to_comment",
    annotations={
        "title": "Reply to a comment",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def facebook_reply_to_comment(params: ReplyInput) -> str:
    """Reply to a specific comment on one of your Page's posts."""
    return await _run_facebook(
        get_fb_client().reply_to_comment(
            params.comment_id, page_id=params.page_id, message=params.message,
        )
    )


class PublishPhotoInput(_StrictModel):
    page_id: Annotated[str, Field(pattern=r"^\d+$")]
    caption: Annotated[
        str | None,
        Field(default=None, max_length=63000, description="Optional caption text."),
    ] = None
    photo_path: Annotated[
        str | None,
        Field(default=None, description="Local path to an image file on the MCP server."),
    ] = None
    photo_url: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Publicly reachable image URL. Facebook fetches the bytes "
                "server-side, so the URL must not require auth."
            ),
        ),
    ] = None
    published: bool = Field(
        default=True,
        description=(
            "If False, the photo is uploaded but not shown on the Page. "
            "Useful for composing multi-photo feed posts."
        ),
    )


@mcp.tool(
    name="facebook_post_photo",
    annotations={
        "title": "Publish a photo to a Page",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def facebook_post_photo(params: PublishPhotoInput) -> str:
    """Publish a photo to a Page. Provide exactly one of photo_path or photo_url."""
    return await _run_facebook(
        get_fb_client().publish_photo(
            params.page_id,
            caption=params.caption,
            photo_path=params.photo_path,
            photo_url=params.photo_url,
            published=params.published,
        )
    )


class PublishVideoInput(_StrictModel):
    page_id: Annotated[str, Field(pattern=r"^\d+$")]
    description: Annotated[
        str | None,
        Field(default=None, max_length=63000),
    ] = None
    video_path: Annotated[
        str | None,
        Field(default=None, description="Local path to a video file on the MCP server."),
    ] = None
    video_url: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Publicly reachable video URL. Videos >1 GB should use Facebook's "
                "resumable upload API instead (not supported by this tool)."
            ),
        ),
    ] = None


@mcp.tool(
    name="facebook_post_video",
    annotations={
        "title": "Publish a video to a Page",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def facebook_post_video(params: PublishVideoInput) -> str:
    """Publish a video to a Page. Provide exactly one of video_path or video_url.

    Facebook processes videos asynchronously; the returned ``id`` is valid
    immediately, but the video may take a few minutes to become viewable.
    """
    return await _run_facebook(
        get_fb_client().publish_video(
            params.page_id,
            description=params.description,
            video_path=params.video_path,
            video_url=params.video_url,
        )
    )


@mcp.tool(
    name="facebook_delete_post",
    annotations={
        "title": "Delete a Page post",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def facebook_delete_post(params: PagePostInput) -> str:
    """Delete a post from one of your Pages. This cannot be undone."""
    return await _run_facebook(
        get_fb_client().delete_post(params.post_id, page_id=params.page_id)
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def run_stdio() -> None:
    """Run the server over stdio (for Claude Desktop, Claude Code, etc.)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    mcp.run()


if __name__ == "__main__":
    run_stdio()
