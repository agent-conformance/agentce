package example;

/* metamodel_version: 1.11.0 */
/* version: 0.1.0 */
import java.net.URI;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZonedDateTime;
import java.util.List;
import lombok.*;

/**
  Engine-computed verification outcome per stream (SPEC 6.6).
**/
@Data
@EqualsAndHashCode(callSuper=false)
public class IntegrityResultPayload extends Payload {

  private String stream;
  private String strength;
  private String status;
  private Integer firstBadIndex;
  private List<String> anchors;


}