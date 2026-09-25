import { Composition, registerRoot } from "remotion";
import "../index.css";
import { StudentGuide, STUDENT_DURATION } from "./StudentGuide";
const R: React.FC = () => (
  <Composition id="StudentGuide" component={StudentGuide} durationInFrames={STUDENT_DURATION} fps={30} width={1920} height={1080} />
);
registerRoot(R);
