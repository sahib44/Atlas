import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const mutationHeaders = { "X-MIST-Request": "1" };

export async function readJson(request: APIRequestContext, path: string) {
  const response = await request.get(`/api${path}`);
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

export async function writeJson(request: APIRequestContext, path: string, data?: object, method = "POST") {
  const response = await request.fetch(`/api${path}`, { method, headers: mutationHeaders, data });
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

export async function approveNomination(request: APIRequestContext, record: { id: string; updated_at: string }) {
  return writeJson(request, `/recruitment/${record.id}/review`, { decision: "approved", version: record.updated_at });
}

export async function addApprovedNomination(page: Page, competitionId: string, name: string, email: string, approver = page.request) {
  const record = await writeJson(page.request, `/competitions/${competitionId}/judges`, {
    name, email, qualifications: "Experienced tournament judge.",
  });
  await approveNomination(approver, record);
  return record;
}
