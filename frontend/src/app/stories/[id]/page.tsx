import { StoryDetailView } from "@/components/story/StoryDetailView";

export default async function StoryPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <StoryDetailView storyId={Number(id)} />;
}
