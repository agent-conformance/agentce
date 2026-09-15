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
  Per-event integrity envelope (SPEC 6.6).
**/
@Data
@EqualsAndHashCode(callSuper=false)
public class IntegrityBlock  {

  private String hash;
  private String prev;
  private String stream;
  private String strength;
  private String sigRef;


}