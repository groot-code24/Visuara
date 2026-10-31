export type MemberRole = 'owner' | 'admin' | 'manager' | 'editor' | 'viewer';

export type Me = {
  id: number; email: string; name: string; org_id: number; member_role: MemberRole;
  organization: string | null; plan: string; plan_name: string;
};

export type Product = {
  id: number; name: string; description: string; category: string; created_at: string; image_url: string;
  asset_report: { width?: number; height?: number; aspect_ratio?: number; has_transparency?: boolean; warnings?: { code: string; message: string }[] };
  has_model: boolean;
};

export type Video = {
  id: number; title: string; duration: number; width: number; height: number; fps: number; analyzed: number;
  size_bytes: number; created_at: string; thumbnail_url: string | null;
};

export type Option = {
  product_id: number; score: number; passes_quality: boolean; quality_status: string; failure_codes: string[];
  components: Record<string, number>; reasons: string[]; orientation: string; duration_s: number;
  frame_second: number; start_second: number; end_second: number; quad: number[][]; product_quad: number[][];
  preview_url?: string; placement_id?: number | null;
};

export type DecisionVideo = {
  video_id: number; title: string; duration: number; width: number; height: number; status: 'assigned' | 'no_fit' | 'error';
  recommendation: Option | null; options: Option[]; explanation?: string; error?: string; keyframes?: number;
};

export type DecisionResult = {
  engine_version: string; weights: Record<string, number>; min_recommend_score: number; spread: boolean;
  semantic_scene_descriptions: boolean;
  products: { id: number; name: string; category: string; aspect: number }[];
  videos: DecisionVideo[];
  matrix: { video_id: number; product_id: number; score: number; passes_quality: boolean }[];
  summary: { videos: number; assigned: number; products_used: number[]; unused_products: number[] };
};

export type DecisionStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED';

export type Decision = {
  id: number; name: string; status: DecisionStatus; progress: number; stage: string;
  error: string | null; created_at: string; finished_at: string | null; product_ids: number[]; video_ids: number[];
  summary?: DecisionResult['summary']; result?: DecisionResult;
};

export type Placement = {
  id: number; video_id: number; product_id: number; status: string; version: number; quality_status: string; fit_status: string;
  frame_second: number; start_second: number; end_second: number; video_title: string; product_name: string; image_url: string;
  created_at: string; decision_id: number | null; render_options: Record<string, unknown>;
};

export type Failure = { code: string; message: string; recommended_action: string; timestamps: number[][] };

export type QualityReport = {
  status: 'PASSED' | 'FAILED'; scene_class: string; mode: string; frames_in_interval: number; frames_validated: number;
  failures: Failure[]; warnings: { code: string; message: string }[]; recommended_action: string; analysis_seconds?: number;
  interval: { start_frame: number; end_frame: number; fps: number };
};

export type Job = {
  id: number; placement_id: number; status: string; renderer: string | null; error: string | null; attempts: number;
  created_at: string; finished_at: string | null; video_title: string; product_name: string; placement_version: number;
  download_url: string | null; validation: { status: string; interval_frames_checked?: number } | null;
};

export type PlacementDetail = {
  placement: Placement; video: { id: number; title: string; duration: number; width: number; height: number; fps: number };
  product: { id: number; name: string; category: string; image_url: string } | null;
  quality: QualityReport | null; rights: { creator_consent: number; revoked_at: string | null } | null;
  versions: { version: number; content_hash: string; created_at: string }[];
  jobs: { id: number; status: string; renderer: string; placement_version: number; error: string | null; created_at: string; finished_at: string | null }[];
  history: { event_type: string; details: Record<string, unknown>; created_at: string }[];
};

export type PlanLimits = {
  name: string; price_usd_month: number | null; decisions_per_month: number | null; renders_per_month: number | null;
  storage_mb: number | null; members: number | null; max_video_seconds: number | null; max_videos_per_decision: number;
};

export type Usage = {
  plan: string; plan_limits: PlanLimits; effective: { storage_mb: number; members: number };
  usage: { storage_bytes: number; videos_uploaded_today: number; active_jobs: number; members: number; decisions_this_month: number; renders_this_month: number };
  plans: Record<string, PlanLimits>;
};

export type Member = { id: number; email: string; name: string; member_role: MemberRole; suspended: boolean; created_at: string };
export type Invitation = { id: number; email: string; member_role: string; status: string; created_at: string };
