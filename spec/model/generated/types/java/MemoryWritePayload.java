package example;

/* metamodel_version: 1.11.0 */
/* version: 0.1.0 */
import java.net.URI;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZonedDateTime;
import java.util.List;
import lombok.*;

@Data
@EqualsAndHashCode(callSuper=false)
public class MemoryWritePayload extends Payload {

  private String store;
  private String recordRef;
  private String provenanceOriginRef;
  private String provenanceOriginClass;
  private String trust;
  private String guardVerdict;


}