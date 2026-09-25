import "./index.css";
import { Composition } from "remotion";
import { StudentGuide, STUDENT_DURATION } from "./student/StudentGuide";
import { AdminGuide, ADMIN_DURATION } from "./admin/AdminGuide";
import { DatabaseGuide, DATABASE_DURATION } from "./database/DatabaseGuide";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="StudentGuide"
        component={StudentGuide}
        durationInFrames={STUDENT_DURATION}
        fps={30}
        width={1920}
        height={1080}
      />
      <Composition
        id="AdminGuide"
        component={AdminGuide}
        durationInFrames={ADMIN_DURATION}
        fps={30}
        width={1920}
        height={1080}
      />
      <Composition
        id="DatabaseGuide"
        component={DatabaseGuide}
        durationInFrames={DATABASE_DURATION}
        fps={30}
        width={1920}
        height={1080}
      />
    </>
  );
};
