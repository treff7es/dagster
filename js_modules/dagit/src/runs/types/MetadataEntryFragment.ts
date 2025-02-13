// @generated
/* tslint:disable */
/* eslint-disable */
// This file was automatically generated and should not be edited.

// ====================================================
// GraphQL fragment: MetadataEntryFragment
// ====================================================

export interface MetadataEntryFragment_EventPathMetadataEntry {
  __typename: "EventPathMetadataEntry";
  label: string;
  description: string | null;
  path: string;
}

export interface MetadataEntryFragment_EventJsonMetadataEntry {
  __typename: "EventJsonMetadataEntry";
  label: string;
  description: string | null;
  jsonString: string;
}

export interface MetadataEntryFragment_EventUrlMetadataEntry {
  __typename: "EventUrlMetadataEntry";
  label: string;
  description: string | null;
  url: string;
}

export interface MetadataEntryFragment_EventTextMetadataEntry {
  __typename: "EventTextMetadataEntry";
  label: string;
  description: string | null;
  text: string;
}

export type MetadataEntryFragment = MetadataEntryFragment_EventPathMetadataEntry | MetadataEntryFragment_EventJsonMetadataEntry | MetadataEntryFragment_EventUrlMetadataEntry | MetadataEntryFragment_EventTextMetadataEntry;
