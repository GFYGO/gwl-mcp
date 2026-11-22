"""共享的参数类型别名（Annotated + pydantic Field）。

集中放在这里有两个原因：

1. MCP 的 inputSchema 由官方 SDK 从函数签名生成，参数说明与约束只有写进
   Field 才会出现在 tools/list 里，因此每条 description 都是给模型看的接口文档；
2. 约束值全部来自 site-back 的实现（不是文档），改一处即可对齐全部工具。

出处示例：
* 组类标识 3 位字母 —— routes/api_v1.py:188（fail('组类标识必须是 3 位字母', 400)）
* page/size —— utils/paging.py（size 夹到 1..100，默认 20）
* role 1..32 且字母开头 —— services/org_join_service.py:89
* note/message/reason ≤200 —— services/org_join_service.py:65（MAX_NOTE）
* tags ≤8 个、每个 ≤24 字 —— services/tags.py:47
* permission_bits 6 位 0/1 —— models.py 的 Document.permission_bits
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

# ----------------------------------------------------------------------
# 枚举
# ----------------------------------------------------------------------
Scope = Literal["public", "group", "private"]
TagMode = Literal["all", "any"]
JoinRequestStatus = Literal["pending", "all", "approved", "rejected"]

#: Visibility 会被多个工具直接引用，因此把说明写进别名本身
Visibility = Annotated[
    Literal["public", "group", "private"],
    Field(description="文档可见性：public=全站（仍受权限位约束），group=组内，private=仅归属者。"),
]
#: 搜索类型。上游只认 discover，这里保留用户文档里的 discover_item，工具层负责映射
SearchType = Literal["document", "organization", "group", "discover", "discover_item"]
ObjectType = Literal["document", "organization", "group", "discover_item"]

# ----------------------------------------------------------------------
# 标识类
# ----------------------------------------------------------------------
CID_PATTERN = "^[A-Za-z]{3}$"
OBJECT_ID_PATTERN = "^[A-Za-z0-9_-]{1,64}$"
PERMISSION_BITS_PATTERN = "^[01]{6}$"

Cid = Annotated[str, Field(description="组类标识：3 位字母（组织或组的 id），例如 dev。", pattern=CID_PATTERN)]
DocId = Annotated[int, Field(description="文档 id（正整数）。", ge=1)]
Rev = Annotated[int, Field(description="修订号 revision（正整数），从 gwl_list_document_revisions 的 revision_num 取。", ge=1)]
RequestId = Annotated[int, Field(description="加入申请 id（正整数）。", ge=1)]
FolderId = Annotated[int, Field(description="文件夹 id。0=根目录，-1=该范围下的全部文件夹（不分层），正整数=指定文件夹。", ge=-1)]
ParentFolderId = Annotated[int, Field(description="父文件夹 id。0=只取根级，正整数=只取该文件夹的直接子级，省略=返回全部层级。", ge=0)]
UserId = Annotated[int, Field(description="用户 id（正整数）。", ge=1)]
Username = Annotated[str, Field(description="登录名（1~64 个字符）。", min_length=1, max_length=64)]
Slug = Annotated[str, Field(description="文档 slug（URL 里的唯一标识）。", min_length=1, max_length=200)]
ObjectId = Annotated[
    str,
    Field(
        description="对象 id，一律用字符串形式（数字 id 也要写成 123 的字符串）："
        "文档=doc_id，组织/组=3 位组类标识，发现条目=条目 id。",
        pattern=OBJECT_ID_PATTERN,
    ),
]

# ----------------------------------------------------------------------
# 分页 / 数量
# ----------------------------------------------------------------------
Page = Annotated[int, Field(description="页码，从 1 开始。非法值由上游回落到 1，不报错。", ge=1, default=1)]
Size = Annotated[int, Field(description="每页条数，1~100；超过 100 上游按 100 处理。", ge=1, le=100, default=20)]
TagLimit = Annotated[int, Field(description="返回标签数上限（1~50），默认 10。", ge=1, le=50, default=10)]
FacetLimit = Annotated[int, Field(description="返回聚合项上限（1~200），默认 50。", ge=1, le=200, default=50)]
NotificationLimit = Annotated[int, Field(description="返回条数上限（1~200），默认 100。", ge=1, le=200, default=100)]

# ----------------------------------------------------------------------
# 文本 / 集合
# ----------------------------------------------------------------------
TagName = Annotated[str, Field(description="标签名（不超过 24 个字符）。", max_length=24)]
TagList = Annotated[list[TagName], Field(description="标签列表，最多 8 个（上游限制）。", max_length=8)]
Note = Annotated[str, Field(description="备注 / 附言（最多 200 个字符，上游超限返回 400）。", max_length=200)]
Role = Annotated[str, Field(description="入组角色名：字母开头，可含数字与下划线，1~32 个字符。默认 member。",
                           pattern="^[A-Za-z][A-Za-z0-9_]{0,31}$", default="member")]
PermissionBits = Annotated[
    str,
    Field(
        description="6 位 0/1 权限位串，与站点文档权限节点一致；不确定就先读回原值再改。",
        pattern=PERMISSION_BITS_PATTERN,
    ),
]
Content = Annotated[str, Field(description="文档正文（Markdown）。")]
Title = Annotated[str, Field(description="标题（1~200 个字符）。", min_length=1, max_length=200)]
Summary = Annotated[str, Field(description="摘要；超过 500 个字符会被上游截断。", max_length=500)]
Icon = Annotated[str, Field(description="图标（emoji 或短串）；超过 50 个字符会被上游截断。", max_length=50)]
Owning = Annotated[
    str,
    Field(
        description="归属：visibility=public 时可省略；group=3 位组类标识；private=用户 id 字符串。"
        "写入受服务端写边界校验，越界返回 403。",
        max_length=50,
    ),
]
