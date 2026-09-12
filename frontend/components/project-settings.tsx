"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import WorkLeaseSettings from "@/components/work-lease-settings";
import TranscriptSettingsPanel from "@/components/transcript-settings";
import CodeReviewSettingsPanel from "@/components/code-review-settings";
import ProjectBackupsPanel from "@/components/project-backups";
import { api, ApiError, errorMessage } from "@/lib/api";
import PromptLibrary from "@/components/prompt-library";
import type { SettingsSection } from "@/lib/settings-navigation";
import type { Project, ProjectSettings } from "@/lib/types";

type Props = {
  section: SettingsSection;
  project?: Project;
  settings: ProjectSettings | null;
  loading: boolean;
  loadError: string;
  onRetry: () => void;
  onSaved: (settings: ProjectSettings) => void;
  onProjectSaved: (project: Project) => void;
  onNotice: (message: string, error?: boolean) => void;
  backupMaximumBytes: number;
  backupRefreshSignal: number;
  onBackupPendingChange: (pending: boolean) => void;
  onTranscriptPendingChange: (pending: boolean) => void;
  onPromptPendingChange: (pending: boolean) => void;
};

type ProjectDetailsDraft = {
  name: string;
  slug: string;
  description: string;
  repositoryUrl: string;
};

function detailsFromProject(project?: Project): ProjectDetailsDraft {
  return {
    name: project?.name ?? "",
    slug: project?.slug ?? "",
    description: project?.description ?? "",
    repositoryUrl: project?.repository_url ?? ""
  };
}

const validSlug = (slug: string) => /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug);

export default function ProjectSettingsPanel({
  section,
  project,
  settings,
  loading,
  loadError,
  onRetry,
  onSaved,
  onProjectSaved,
  onNotice,
  backupMaximumBytes,
  backupRefreshSignal,
  onBackupPendingChange,
  onTranscriptPendingChange,
  onPromptPendingChange
}: Props) {
  const [projectDetails, setProjectDetails] = useState(() => detailsFromProject(project));
  const lastProject = useRef(project);
  const projectRequestGeneration = useRef(0);
  const [projectSaving, setProjectSaving] = useState(false);
  const [projectSaveError, setProjectSaveError] = useState("");

  useEffect(() => {
    const previous = lastProject.current;
    lastProject.current = project;
    const next = detailsFromProject(project);
    setProjectDetails((current) => {
      if (!project || !previous || previous.id !== project.id) return next;
      const prior = detailsFromProject(previous);
      return {
        name: current.name === prior.name ? next.name : current.name,
        slug: current.slug === prior.slug ? next.slug : current.slug,
        description: current.description === prior.description
          ? next.description
          : current.description,
        repositoryUrl: current.repositoryUrl === prior.repositoryUrl
          ? next.repositoryUrl
          : current.repositoryUrl
      };
    });
  }, [project]);

  useEffect(() => {
    setProjectSaving(false);
    setProjectSaveError("");
    return () => {
      projectRequestGeneration.current += 1;
    };
  }, [project?.id]);

  if (!project) {
    return <section className="empty-state settings-empty">
      <h2>Select a project.</h2>
      <p>Project settings become available after you create or select a workspace.</p>
      <a className="button button-primary" href="/">Open the work library</a>
    </section>;
  }
  const selectedProject = project;
  const projectDirty = projectDetails.name !== selectedProject.name
    || projectDetails.slug !== selectedProject.slug
    || projectDetails.description !== selectedProject.description
    || projectDetails.repositoryUrl !== (selectedProject.repository_url ?? "");

  async function saveProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const generation = ++projectRequestGeneration.current;
    setProjectSaving(true);
    setProjectSaveError("");
    try {
      const saved = await api<Project>(
        `/projects/${encodeURIComponent(selectedProject.id)}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            name: projectDetails.name,
            slug: projectDetails.slug,
            description: projectDetails.description,
            repository_url: projectDetails.repositoryUrl.trim() || null
          })
        }
      );
      if (generation !== projectRequestGeneration.current) return;
      setProjectDetails(detailsFromProject(saved));
      onProjectSaved(saved);
      onNotice(`Project details saved for “${saved.name}”.`);
    } catch (error) {
      if (generation !== projectRequestGeneration.current) return;
      setProjectSaveError(errorMessage(error));
      if (!(error instanceof ApiError) || error.status === 0 || error.status >= 500) {
        setProjectSaveError(
          "The save outcome is uncertain. Reload this page and compare the project before retrying."
        );
      }
    } finally {
      if (generation === projectRequestGeneration.current) setProjectSaving(false);
    }
  }

  return <div className="settings-stack">
    {section === "code-reviews" && <CodeReviewSettingsPanel key={selectedProject.id} projectId={selectedProject.id} settings={settings} loading={loading} onSaved={onSaved} onRetry={onRetry} onNotice={onNotice} />}
    {section === "workspace" && <section className="settings-card" aria-labelledby="project-details-title">
      <div className="settings-card-heading">
        <div>
          <span className="section-label">WORKSPACE DETAILS</span>
          <h2 id="project-details-title">Project details</h2>
        </div>
      </div>
      <p className="settings-intro">
        Keep the project identity and repository location current for people and agents.
      </p>
      <form
        className="form-stack settings-project-form"
        onSubmit={(event) => void saveProject(event)}
      >
        <label className="field" htmlFor="project-settings-name">
          Project name
          <input
            id="project-settings-name"
            required
            maxLength={120}
            value={projectDetails.name}
            disabled={projectSaving}
            onChange={(event) => {
              setProjectDetails((current) => ({ ...current, name: event.target.value }));
              setProjectSaveError("");
            }}
          />
        </label>
        <div className="field">
          <label htmlFor="project-settings-slug">Project slug</label>
          <input
            id="project-settings-slug"
            required
            maxLength={100}
            pattern="[a-z0-9]+(-[a-z0-9]+)*"
            aria-describedby="project-settings-slug-hint"
            value={projectDetails.slug}
            disabled={projectSaving}
            onChange={(event) => {
              setProjectDetails((current) => ({ ...current, slug: event.target.value }));
              setProjectSaveError("");
            }}
          />
          <span className="field-hint" id="project-settings-slug-hint">
            Lowercase letters, numbers, and single hyphens only.
          </span>
        </div>
        <label className="field" htmlFor="project-settings-description">
          Description <span className="optional">Optional</span>
          <textarea
            id="project-settings-description"
            rows={3}
            maxLength={4000}
            value={projectDetails.description}
            disabled={projectSaving}
            onChange={(event) => {
              setProjectDetails((current) => ({ ...current, description: event.target.value }));
              setProjectSaveError("");
            }}
          />
        </label>
        <label className="field" htmlFor="project-settings-repository-url">
          Repository URL <span className="optional">Optional</span>
          <input
            id="project-settings-repository-url"
            type="url"
            maxLength={2000}
            value={projectDetails.repositoryUrl}
            disabled={projectSaving}
            onChange={(event) => {
              setProjectDetails((current) => ({
                ...current,
                repositoryUrl: event.target.value
              }));
              setProjectSaveError("");
            }}
          />
        </label>
        {projectSaveError && <div className="error-notice" role="alert">
          <p>{projectSaveError}</p>
        </div>}
        <div className="settings-actions">
          <button
            className="button button-primary"
            type="submit"
            disabled={projectSaving || !projectDirty || !projectDetails.name.trim()
              || !validSlug(projectDetails.slug)}
          >
            {projectSaving ? "Saving…" : "Save project details"}
          </button>
        </div>
      </form>
      <WorkLeaseSettings key={selectedProject.id} projectId={selectedProject.id}
        settings={settings} loading={loading} loadError={loadError}
        onSaved={onSaved} onRetry={onRetry} onNotice={onNotice} />
    </section>}
    {section === "workspace" && <TranscriptSettingsPanel key={selectedProject.id} projectId={selectedProject.id} onPendingChange={onTranscriptPendingChange} />}
    {section === "prompts" && <PromptLibrary key={selectedProject.id} project={selectedProject} onNotice={onNotice} onPendingChange={onPromptPendingChange} />}
    {section === "backups" && <ProjectBackupsPanel key={selectedProject.id} project={selectedProject} maximumBytes={backupMaximumBytes} refreshSignal={backupRefreshSignal} onPendingChange={onBackupPendingChange} />}
  </div>;
}
