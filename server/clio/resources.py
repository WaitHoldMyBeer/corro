"""Which Clio endpoints are read for a matter, and which fields are asked for.

Clio returns only `id` and `etag` unless `fields` names more, so each resource
lists its fields here. Nothing in this file names a matter: the matter id is a
run-time argument.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

WHO_AM_I = ("users/who_am_i.json", "id,name,email")

MATTER_LIST_FIELDS = "id,display_number,description,status,updated_at,client{id,name},matter_stage{id,name}"

MATTER_FIELDS = (
    "id,etag,display_number,description,status,open_date,close_date,pending_date,created_at,updated_at,"
    "last_activity_date,matter_stage_updated_at,"
    "client{id,name,type},practice_area{id,name},matter_stage{id,name},"
    "responsible_attorney{id,name},originating_attorney{id,name},"
    "statute_of_limitations{id,name,due_at,status},"
    # Clio allows one level of nesting only, so custom_field comes back as its default {id, etag}.
    "custom_field_values{id,etag,field_name,field_type,field_display_order,value,created_at,updated_at,"
    "custom_field,picklist_option}"
)

CONTACT_FIELDS = (
    "id,etag,name,first_name,last_name,type,title,prefix,date_of_birth,is_client,created_at,updated_at,"
    "primary_email_address,primary_phone_number,company{id,name},avatar{id,url},"
    "email_addresses{id,name,address,primary},phone_numbers{id,name,number,primary},"
    "addresses{id,name,street,city,province,postal_code,country}"
)


@dataclass(frozen=True)
class Resource:
    kind: str  # the name the object is stored under
    path: str
    fields: str
    params: Callable[[int], dict[str, Any]] = field(default=lambda matter_id: {"matter_id": matter_id})
    # Some endpoints mix object types; `kind_of` files each row under its own kind.
    kind_of: Callable[[dict[str, Any]], str] | None = None
    kinds: tuple[str, ...] = ()
    # An optional resource that Clio refuses becomes a warning instead of a failed sync.
    optional: bool = False

    def all_kinds(self) -> tuple[str, ...]:
        return self.kinds or (self.kind,)


# Clio's activity types that are money the firm laid out, as opposed to time.
EXPENSE_ACTIVITY_TYPES = frozenset({"ExpenseEntry", "HardCostEntry", "SoftCostEntry"})


def _activity_kind(row: dict[str, Any]) -> str:
    return "expense" if row.get("type") in EXPENSE_ACTIVITY_TYPES else "time_entry"


# Everything read for one matter, in sync order.
MATTER_RESOURCES: tuple[Resource, ...] = (
    Resource(
        "custom_field",
        "custom_fields.json",
        "id,etag,name,parent_type,field_type,displayed,deleted,required,display_order,created_at,updated_at,"
        "picklist_options{id,option}",
        params=lambda matter_id: {"parent_type": "matter"},
    ),
    Resource(
        "relationship",
        "relationships.json",
        "id,etag,description,created_at,updated_at,contact{id,name,type},matter{id}",
    ),
    Resource(
        "note",
        "notes.json",
        "id,etag,type,subject,detail,detail_text_type,date,created_at,updated_at,author{id,name},matter{id}",
        params=lambda matter_id: {"type": "Matter", "matter_id": matter_id},
    ),
    Resource(
        "communication",
        "communications.json",
        "id,etag,type,subject,body,date,received_at,created_at,updated_at,user{id,name},matter{id},"
        "senders{id,type,name},receivers{id,type,name}",
    ),
    Resource(
        "task",
        "tasks.json",
        "id,etag,name,description,status,priority,due_at,completed_at,statute_of_limitations,"
        "created_at,updated_at,assignee{id,type,name},assigner{id,name},task_type{id,name},matter{id}",
    ),
    Resource(
        "calendar_entry",
        "calendar_entries.json",
        "id,etag,summary,description,location,start_at,end_at,all_day,recurrence_rule,created_at,updated_at,"
        "calendar_owner{id,name,type},attendees{id,type,name},matter{id}",
        # Every user's entries for the matter, not only the connected user's calendar.
        params=lambda matter_id: {"matter_id": matter_id, "owner_entries_across_all_users": "true"},
    ),
    Resource(
        "activity",
        "activities.json",
        "id,etag,type,date,quantity,price,total,note,billed,non_billable,created_at,updated_at,"
        "user{id,name},expense_category{id,name},vendor{id,name},matter{id}",
        kind_of=_activity_kind,
        kinds=("expense", "time_entry"),
    ),
    Resource(
        "folder",
        "folders.json",
        "id,etag,name,type,root,created_at,updated_at,parent{id,type,name}",
        optional=True,
    ),
    # Billing and trust: read when the firm's Clio permissions allow it; otherwise skipped with a warning.
    Resource(
        "bill",
        "bills.json",
        "id,etag,number,subject,state,kind,type,issued_at,due_at,paid_at,total,paid,pending,due,balance,sub_total,"
        "created_at,updated_at,client{id,name}",
        optional=True,
    ),
    Resource(
        "bank_transaction",
        "bank_transactions.json",
        "id,etag,type,transaction_type,date,amount,funds_in,funds_out,description,source,confirmation,"
        "created_at,updated_at,bank_account{id,name,type},client{id,name},matter{id}",
        optional=True,
    ),
    Resource(
        "document",
        "documents.json",
        "id,etag,name,filename,content_type,size,type,locked,received_at,created_at,updated_at,"
        "parent{id,type,name},document_category{id,name},"
        "latest_document_version{id,uuid,filename,size,content_type,fully_uploaded,created_at,received_at}",
    ),
)

# Object types whose counts the sync prints, in the order the firm thinks of them.
REPORTED_KINDS = (
    "custom_field",
    "contact",
    "note",
    "communication",
    "task",
    "calendar_entry",
    "expense",
    "document",
    "relationship",
    "folder",
    "time_entry",
    "bill",
    "bank_transaction",
    "matter",
)
